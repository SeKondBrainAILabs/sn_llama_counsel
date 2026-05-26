"""
runner.py — Memory-aware smart-scheduled LLM fan-out and chairperson synthesis.

Scheduling strategy:
  1. Members are grouped by model (same model → same llama-server process).
  2. Available system memory is checked before each scheduling decision.
  3. Model groups that FIT in available memory run in PARALLEL.
  4. When a batch would exceed available memory, it starts a new sequential batch.
  5. Chairperson loads only for synthesis after all members finish.

This maximizes parallelism when RAM allows (e.g. two 8b models together)
while preventing OOM when it doesn't (e.g. 70b + 32b on 96GB).

Persistence & cancellation:
  - When a PersistenceStore is provided, each run is recorded with its
    session, parent run, counsel snapshot, member responses, synthesis and
    usage totals. Partial runs are written on client disconnect / cancel.
  - Client disconnect propagates as asyncio.CancelledError into the
    async generator; we cancel in-flight member tasks, emit a `cancelled`
    event, and finalize the run with status="cancelled".

Usage:
  - All llama-server calls set stream_options.include_usage=True. The
    final usage chunk from each stream is aggregated and emitted as
    `{"type":"usage","role":...,"prompt_tokens":n,"completion_tokens":n}`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from collections import OrderedDict
from collections.abc import AsyncGenerator
from typing import Any, Optional

import httpx

from .persistence import PersistenceStore
from .schemas import (
    CounselChairperson,
    CounselConfig,
    CounselMember,
    MemberOverride,
)

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
_RESERVED_TOKENS = 2048
_CHARS_PER_TOKEN = 4
_DEFAULT_CTX_TOKENS = 8192
_SYSTEM_RESERVE_GB = 20  # Always reserve this much for OS + other apps
_DEFAULT_MEMBER_TEMP = 0.7
_DEFAULT_CHAIR_TEMP = 0.5


# ── Memory helpers ─────────────────────────────────────────────────────────────

def _get_available_memory_gb() -> float:
    """Get available memory in GB (free + inactive pages on macOS)."""
    try:
        result = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5)
        page_size = 16384  # Apple Silicon
        free_pages = 0
        inactive_pages = 0
        for line in result.stdout.split("\n"):
            if "Pages free" in line:
                free_pages = int(line.split(":")[1].strip().rstrip("."))
            elif "Pages inactive" in line:
                inactive_pages = int(line.split(":")[1].strip().rstrip("."))
        return (free_pages + inactive_pages) * page_size / (1024**3)
    except Exception:
        return 32.0  # conservative fallback


# Model size estimates in GB (includes KV cache overhead)
_MODEL_SIZE_MAP: dict[str, float] = {
    "72b": 48, "70b": 48,
    "32b": 22, "34b": 24,
    "14b": 12, "13b": 12,
    "8b": 7, "7b": 6,
    "3b": 3, "1b": 1.5,
    "embed": 0.5,
}


def _estimate_model_gb(model_name: str) -> float:
    """Estimate RAM needed for a model from its name."""
    name_lower = model_name.lower()
    for pattern, gb in _MODEL_SIZE_MAP.items():
        if pattern in name_lower:
            return gb
    return 12.0  # conservative default for unknown models


def _max_chunk_chars(ctx_tokens: int) -> int:
    return max(4000, (ctx_tokens - _RESERVED_TOKENS) * _CHARS_PER_TOKEN)


# ── SSE helpers ────────────────────────────────────────────────────────────────

def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"


# ── File chunking ─────────────────────────────────────────────────────────────

def _chunk_files(
    files: list[dict] | None,
    max_chars: int = 24_000,
) -> list[list[dict]]:
    """Split file content parts into batches ≤ max_chars of text."""
    if not files:
        return []

    image_parts = [f for f in files if f.get("type") == "image_url"]
    text_parts = [f for f in files if f.get("type") == "text"]
    total_text = sum(len(f.get("text", "")) for f in text_parts)

    if total_text <= max_chars:
        return [files]

    chunks: list[list[dict]] = []
    current: list[dict] = list(image_parts)
    current_size = 0

    for part in text_parts:
        text = part.get("text", "")
        part_len = len(text)

        if current_size + part_len <= max_chars:
            current.append(part)
            current_size += part_len
        elif part_len <= max_chars:
            if current:
                chunks.append(current)
            current = [part]
            current_size = part_len
        else:
            if current:
                chunks.append(current)
                current = []
                current_size = 0
            n_sub = (part_len + max_chars - 1) // max_chars
            for idx in range(n_sub):
                start = idx * max_chars
                end = min(start + max_chars, part_len)
                segment = text[start:end]
                header = f"\n[Part {idx + 1}/{n_sub}]\n" if n_sub > 1 else ""
                chunks.append([{"type": "text", "text": header + segment}])

    if current:
        chunks.append(current)
    return chunks if chunks else [files]


# ── Overrides ─────────────────────────────────────────────────────────────────

def _apply_overrides(
    counsel: CounselConfig,
    overrides: Optional[dict[str, MemberOverride]],
) -> CounselConfig:
    """Return a deep-copied counsel with per-role overrides applied."""
    if not overrides:
        return counsel
    snap = counsel.model_copy(deep=True)
    for member in snap.members:
        o = overrides.get(member.role)
        if not o:
            continue
        if o.model is not None:
            member.model = o.model
        if o.system is not None:
            member.system = o.system
        if o.temperature is not None:
            member.temperature = o.temperature
        if o.max_tokens is not None:
            member.max_tokens = o.max_tokens
    return snap


# ── Follow-up context ─────────────────────────────────────────────────────────

def _format_follow_up_prefix(chain: list[dict[str, Any]]) -> str:
    """Render prior turns from a run chain as a text preamble.

    Each entry in `chain` is the dict returned by PersistenceStore.get_run
    and contains `task`, `synthesis`, and `members` (list of dicts).
    The chain is in chronological order (oldest first).
    """
    if not chain:
        return ""
    parts: list[str] = [
        "You are continuing a prior counsel deliberation. Here is the history "
        "so far — use it as context for the NEW task at the end. Do not repeat "
        "prior analysis verbatim; build on it.",
    ]
    for i, turn in enumerate(chain, 1):
        parts.append(f"\n── Turn {i} ──")
        parts.append(f"User task: {turn.get('task', '')}")
        members = turn.get("members") or []
        if members:
            parts.append("Member responses:")
            for m in members:
                role = m.get("role", "")
                content = (m.get("content") or "").strip()
                if content:
                    parts.append(f"  [{role}] {content}")
        synth = (turn.get("synthesis") or "").strip()
        if synth:
            parts.append(f"Chair synthesis: {synth}")
    parts.append("\n── New task ──")
    return "\n".join(parts) + "\n"


# ── Streaming helpers ─────────────────────────────────────────────────────────

async def _do_stream_member(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    member: CounselMember,
    user_content: str | list[dict],
    queue: asyncio.Queue,
) -> tuple[str, int, int]:
    """Stream one member request.

    Returns (full_text, prompt_tokens, completion_tokens). The token counts
    come from the final `usage` chunk when the server honours
    `stream_options.include_usage`; otherwise they're zero.
    """
    full_text = ""
    prompt_tokens = 0
    completion_tokens = 0
    messages = [
        {"role": "system", "content": member.system},
        {"role": "user", "content": user_content},
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": member.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": member.temperature if member.temperature is not None else _DEFAULT_MEMBER_TEMP,
    }
    if member.max_tokens is not None:
        payload["max_tokens"] = member.max_tokens

    async with client.stream(
        "POST",
        f"{api_base}/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            usage = chunk.get("usage")
            if usage:
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            # Reasoning models (Qwen3, QwQ) emit thinking in
            # "reasoning_content" before emitting "content".
            # Stream both so the UI is never blank.
            token = delta.get("content") or ""
            reasoning = delta.get("reasoning_content") or ""
            combined = token or reasoning
            if combined:
                full_text += combined
                await queue.put(
                    _sse({
                        "type": "member_token",
                        "role": member.role,
                        "model": member.model,
                        "token": combined,
                    })
                )
    return full_text, prompt_tokens, completion_tokens


def _build_user_content(task: str, files: list[dict] | None) -> str | list[dict]:
    if not files:
        return task
    user_content: list[dict] = [{"type": "text", "text": task}]
    user_content.extend(files)
    return user_content


def _strip_to_text_only(files: list[dict] | None) -> str | None:
    if not files:
        return None
    text_parts = [f.get("text", "") for f in files if f.get("type") == "text" and f.get("text")]
    return "\n".join(text_parts) if text_parts else None


async def _stream_chunk_with_fallback(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    member: CounselMember,
    task: str,
    chunk_files: list[dict],
    queue: asyncio.Queue,
) -> tuple[str, int, int]:
    """Stream a single chunk with 400-fallback: full → text-only → plain task."""
    try:
        user_content = _build_user_content(task, chunk_files)
        return await _do_stream_member(client, api_base, api_key, member, user_content, queue)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 400 or not chunk_files:
            raise
        logger.warning("Member %s got 400 — trying text-only fallback", member.role)

    text_supplement = _strip_to_text_only(chunk_files)
    if text_supplement:
        try:
            return await _do_stream_member(
                client, api_base, api_key, member, f"{task}\n{text_supplement}", queue
            )
        except httpx.HTTPStatusError as exc2:
            if exc2.response.status_code != 400:
                raise
            logger.warning("Member %s still 400 — trying plain task", member.role)

    return await _do_stream_member(client, api_base, api_key, member, task, queue)


async def _stream_member(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    member: CounselMember,
    task: str,
    files: list[dict] | None,
    queue: asyncio.Queue,
) -> dict[str, Any]:
    """Stream a single member's response with chunking support.

    Returns a dict: {"role", "model", "content", "error", "prompt_tokens",
    "completion_tokens"}.
    """
    try:
        max_chars = _max_chunk_chars(member.ctx_tokens or _DEFAULT_CTX_TOKENS)
        file_chunks = _chunk_files(files, max_chars)

        full_text = ""
        prompt_total = 0
        completion_total = 0

        if len(file_chunks) <= 1:
            text, pt, ct = await _stream_chunk_with_fallback(
                client, api_base, api_key, member, task,
                file_chunks[0] if file_chunks else [],
                queue,
            )
            full_text = text
            prompt_total = pt
            completion_total = ct
        else:
            n = len(file_chunks)
            for i, chunk_parts in enumerate(file_chunks):
                if i > 0:
                    sep = f"\n\n━━━ Section {i + 1}/{n} ━━━\n\n"
                    full_text += sep
                    await queue.put(
                        _sse({"type": "member_token", "role": member.role, "model": member.model, "token": sep})
                    )
                chunk_task = f"{task}\n\n[Analysing section {i + 1} of {n} — focus on this section's content]"
                text, pt, ct = await _stream_chunk_with_fallback(
                    client, api_base, api_key, member, chunk_task, chunk_parts, queue,
                )
                full_text += text
                prompt_total += pt
                completion_total += ct

        await queue.put(
            _sse({
                "type": "usage",
                "role": member.role,
                "prompt_tokens": prompt_total,
                "completion_tokens": completion_total,
            })
        )
        await queue.put(_sse({"type": "member_done", "role": member.role, "model": member.model}))
        return {
            "role": member.role,
            "model": member.model,
            "content": full_text,
            "error": None,
            "prompt_tokens": prompt_total,
            "completion_tokens": completion_total,
        }

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Member %s error: %s", member.role, exc)
        await queue.put(_sse({"type": "member_error", "role": member.role, "error": str(exc)}))
        return {
            "role": member.role,
            "model": member.model,
            "content": "",
            "error": str(exc),
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }


# ── Chairperson synthesis ─────────────────────────────────────────────────────

async def _stream_synthesis(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    chairperson: CounselChairperson,
    task: str,
    member_responses: dict[str, str],
    queue: asyncio.Queue,
    prior_context: str = "",
) -> tuple[str, int, int]:
    """Stream the chairperson's synthesis.

    Returns (full_text, prompt_tokens, completion_tokens).
    """
    responses_block = "\n\n".join(
        f"### {role}\n{text}" for role, text in member_responses.items() if text
    )
    preface = prior_context if prior_context else ""
    user_content = (
        f"{preface}"
        f"Original task:\n{task}\n\n"
        f"Council responses:\n{responses_block}\n\n"
        f"Please synthesize the above into a consolidated, actionable response."
    )

    messages = [
        {"role": "system", "content": chairperson.system},
        {"role": "user", "content": user_content},
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload: dict[str, Any] = {
        "model": chairperson.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": chairperson.temperature if chairperson.temperature is not None else _DEFAULT_CHAIR_TEMP,
    }
    if chairperson.max_tokens is not None:
        payload["max_tokens"] = chairperson.max_tokens

    full_text = ""
    prompt_tokens = 0
    completion_tokens = 0

    try:
        async with client.stream(
            "POST",
            f"{api_base}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=300.0,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                usage = chunk.get("usage")
                if usage:
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                # Reasoning models (Qwen3, QwQ) emit thinking in
                # "reasoning_content" before emitting "content".
                token = delta.get("content") or ""
                reasoning = delta.get("reasoning_content") or ""
                combined = token or reasoning
                if combined:
                    full_text += combined
                    await queue.put(_sse({"type": "synthesis_token", "token": combined}))
        await queue.put(
            _sse({
                "type": "usage",
                "role": "__chair__",
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            })
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("Chairperson synthesis error: %s", exc)
        await queue.put(_sse({"type": "error", "error": f"Chairperson error: {exc}"}))
    return full_text, prompt_tokens, completion_tokens


# ── Smart scheduling ──────────────────────────────────────────────────────────

def _group_members_by_model(
    members: list[CounselMember],
) -> OrderedDict[str, list[CounselMember]]:
    """Group members by model name, preserving first-appearance order."""
    groups: OrderedDict[str, list[CounselMember]] = OrderedDict()
    for m in members:
        groups.setdefault(m.model, []).append(m)
    return groups


def _plan_batches(
    model_groups: OrderedDict[str, list[CounselMember]],
) -> list[list[tuple[str, list[CounselMember]]]]:
    """Plan execution batches based on available memory."""
    available_gb = _get_available_memory_gb()
    usable_gb = max(available_gb - _SYSTEM_RESERVE_GB, 10.0)  # at least 10GB

    logger.info(
        "Memory planning: %.1f GB available, %.1f GB usable (%.0f GB reserved for system)",
        available_gb, usable_gb, _SYSTEM_RESERVE_GB,
    )

    batches: list[list[tuple[str, list[CounselMember]]]] = []
    current_batch: list[tuple[str, list[CounselMember]]] = []
    current_gb = 0.0

    for model_name, members in model_groups.items():
        model_gb = _estimate_model_gb(model_name)
        if current_gb + model_gb <= usable_gb:
            current_batch.append((model_name, members))
            current_gb += model_gb
        else:
            if current_batch:
                batches.append(current_batch)
            current_batch = [(model_name, members)]
            current_gb = model_gb

    if current_batch:
        batches.append(current_batch)

    for i, batch in enumerate(batches):
        models = [name for name, _ in batch]
        total_gb = sum(_estimate_model_gb(n) for n, _ in batch)
        logger.info("  Batch %d: %s (≈%.0f GB)", i + 1, " + ".join(models), total_gb)

    return batches


async def _drain_queue(queue: asyncio.Queue) -> list[str]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_counsel(
    task: str,
    counsel: CounselConfig,
    api_base: str,
    api_key: str,
    files: list[dict] | None = None,
    *,
    store: Optional[PersistenceStore] = None,
    session_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
    member_overrides: Optional[dict[str, MemberOverride]] = None,
) -> AsyncGenerator[str, None]:
    """
    Main SSE generator for a counsel run.

    If `store` and `session_id` are provided the run is persisted. Member
    overrides are applied to a deep copy of the counsel before execution,
    so the persisted counsel_snapshot reflects what actually ran.
    Parent run chain (if `parent_run_id` provided) is loaded and prepended
    to each member and the chairperson as prior turn context.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()

    effective_counsel = _apply_overrides(counsel, member_overrides)

    # Resolve prior context for follow-ups.
    prior_context = ""
    if store and parent_run_id:
        try:
            chain = await store.get_run_chain(parent_run_id)
            prior_context = _format_follow_up_prefix(chain)
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to load parent chain %s: %s", parent_run_id, exc)

    effective_task = f"{prior_context}{task}" if prior_context else task

    # Persistence: create run row up-front so cancellations are recorded.
    run_id: Optional[str] = None
    if store and session_id:
        try:
            await store.touch_session(session_id, title_if_empty=task[:80])
            run_id = await store.create_run(
                session_id=session_id,
                task=task,
                counsel_snapshot=effective_counsel.model_dump(),
                parent_run_id=parent_run_id,
            )
            yield _sse({"type": "run_created", "run_id": run_id, "session_id": session_id})
        except Exception as exc:
            logger.error("Failed to create run record: %s", exc)

    # Per-member results + aggregated usage for persistence.
    member_results: dict[str, dict[str, Any]] = {}
    aggregated_usage: dict[str, dict[str, int]] = {}
    synthesis_text = ""
    final_status = "completed"
    pending_tasks: set[asyncio.Task] = set()

    async def _finalize(status: str):
        if not (store and run_id):
            return
        try:
            await store.finalize_run(
                run_id=run_id,
                synthesis=synthesis_text,
                status=status,
                member_responses={
                    role: {
                        "model": info.get("model", ""),
                        "content": info.get("content", ""),
                        "error": info.get("error"),
                    }
                    for role, info in member_results.items()
                },
                usage={
                    role: {
                        "prompt": u.get("prompt", 0),
                        "completion": u.get("completion", 0),
                    }
                    for role, u in aggregated_usage.items()
                },
            )
        except Exception as exc:
            logger.error("Failed to finalize run %s: %s", run_id, exc)

    try:
        async with httpx.AsyncClient() as client:
            # ── Phase 0: Retrieval pre-phase ──────────────────────────────
            # Members marked retrieval=True run first, once, and their
            # output is prepended as retrieved context to the remaining
            # members and the chairperson. They never appear in the main
            # batches. This lets RAG-style lookups happen once per run.
            retrieval_members = [m for m in effective_counsel.members if m.retrieval]
            main_members = [m for m in effective_counsel.members if not m.retrieval]
            retrieved_context = ""
            for rmember in retrieval_members:
                result = await _stream_member(
                    client, api_base, api_key, rmember,
                    effective_task, files, queue,
                )
                member_results[rmember.role] = result
                aggregated_usage[rmember.role] = {
                    "prompt": result.get("prompt_tokens", 0),
                    "completion": result.get("completion_tokens", 0),
                }
                for item in await _drain_queue(queue):
                    yield item
                if result.get("content"):
                    retrieved_context += f"\n\n[Retrieved by {rmember.role}]\n{result['content']}"

            main_task = (
                f"{effective_task}\n\n[Retrieved context]{retrieved_context}"
                if retrieved_context
                else effective_task
            )

            # ── Phase 1: Run members by memory-aware batches ──────────────
            model_groups = _group_members_by_model(main_members)
            batches = _plan_batches(model_groups)

            for batch_idx, batch in enumerate(batches):
                n_batches = len(batches)
                batch_models = [name for name, _ in batch]
                logger.info(
                    "Running batch %d/%d: %s",
                    batch_idx + 1, n_batches, ", ".join(batch_models),
                )

                batch_tasks: list[asyncio.Task] = []
                batch_members: list[CounselMember] = []

                for _model_name, group_members in batch:
                    for member in group_members:
                        batch_members.append(member)
                        t = asyncio.create_task(
                            _stream_member(
                                client, api_base, api_key, member,
                                main_task, files, queue,
                            )
                        )
                        batch_tasks.append(t)
                        pending_tasks.add(t)

                pending = set(batch_tasks)
                while pending:
                    for item in await _drain_queue(queue):
                        yield item
                    done, pending = await asyncio.wait(
                        pending, timeout=0.05, return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in done:
                        pending_tasks.discard(t)

                for item in await _drain_queue(queue):
                    yield item

                for member, t in zip(batch_members, batch_tasks):
                    try:
                        result = t.result()
                    except Exception as exc:
                        result = {
                            "role": member.role,
                            "model": member.model,
                            "content": "",
                            "error": str(exc),
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                        }
                    member_results[member.role] = result
                    aggregated_usage[member.role] = {
                        "prompt": result.get("prompt_tokens", 0),
                        "completion": result.get("completion_tokens", 0),
                    }

            yield _sse({"type": "members_done"})

            # ── Phase 2: Chairperson synthesis ────────────────────────────
            synth_future = asyncio.create_task(
                _stream_synthesis(
                    client, api_base, api_key,
                    effective_counsel.chairperson, task,
                    {role: r["content"] for role, r in member_results.items()},
                    queue,
                    prior_context=prior_context,
                )
            )
            pending_tasks.add(synth_future)

            while not synth_future.done():
                for item in await _drain_queue(queue):
                    yield item
                try:
                    await asyncio.wait_for(asyncio.shield(synth_future), timeout=0.05)
                except asyncio.TimeoutError:
                    pass

            pending_tasks.discard(synth_future)
            for item in await _drain_queue(queue):
                yield item

            try:
                synthesis_text, chair_prompt, chair_completion = synth_future.result()
            except Exception as exc:
                logger.error("Synthesis task failed: %s", exc)
                synthesis_text = ""
                chair_prompt = chair_completion = 0
            aggregated_usage["__chair__"] = {
                "prompt": chair_prompt,
                "completion": chair_completion,
            }

        await _finalize("completed")
        if run_id:
            yield _sse({"type": "run_saved", "run_id": run_id, "status": "completed"})
        yield _sse({"type": "done"})

    except asyncio.CancelledError:
        final_status = "cancelled"
        logger.info("Counsel run %s cancelled by client", run_id or "<no-id>")
        for t in list(pending_tasks):
            if not t.done():
                t.cancel()
        for t in list(pending_tasks):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Record whatever we have so far.
        for item in await _drain_queue(queue):
            yield item
        await _finalize("cancelled")
        yield _sse({"type": "cancelled", "run_id": run_id})
        raise
    except Exception as exc:
        final_status = "error"
        logger.exception("Counsel run failed: %s", exc)
        yield _sse({"type": "error", "error": str(exc)})
        await _finalize("error")
