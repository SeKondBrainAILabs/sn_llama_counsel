"""
runner.py — Parallel LLM fan-out and chairperson synthesis.

Phase 1: All council members are queried concurrently with asyncio.
         SSE events stream each member's tokens as they arrive.
         Large file content is chunked adaptively per model context size —
         each member processes ALL chunks sequentially.

Phase 2: Once all members finish, the chairperson receives the full
         task + all member responses and streams its synthesis.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from .schemas import CounselConfig, CounselMember, CounselChairperson

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
# Reserve ~2048 tokens for system prompt + task text + generation headroom.
# At ~4 chars per token, usable content chars = (ctx_tokens - 2048) * 4.
_RESERVED_TOKENS = 2048
_CHARS_PER_TOKEN = 4
_DEFAULT_CTX_TOKENS = 8192  # safe minimum when ctx_tokens not specified


def _max_chunk_chars(ctx_tokens: int) -> int:
    """Compute the maximum text characters for a single chunk."""
    return max(4000, (ctx_tokens - _RESERVED_TOKENS) * _CHARS_PER_TOKEN)


# ── SSE helpers ────────────────────────────────────────────────────────────────

def _sse(event: dict[str, Any]) -> str:
    """Format a dict as a Server-Sent Events data line."""
    return f"data: {json.dumps(event)}\n\n"


# ── File chunking ─────────────────────────────────────────────────────────────

def _chunk_files(
    files: list[dict] | None,
    max_chars: int = 24_000,
) -> list[list[dict]]:
    """Split file content parts into batches that fit within *max_chars* of text.

    Rules:
    - image_url parts go in the first chunk only (they don't count toward text budget).
    - Whole text parts are kept together when they fit.
    - A single text part larger than max_chars is split with [Part N/M] headers.
    - Returns a list of chunk batches: [[parts...], [parts...], ...]
    - Returns [[]] (one empty chunk) when files is None/empty so callers
      can iterate uniformly.
    """
    if not files:
        return []

    image_parts = [f for f in files if f.get("type") == "image_url"]
    text_parts = [f for f in files if f.get("type") == "text"]

    total_text = sum(len(f.get("text", "")) for f in text_parts)

    # Fast path: everything fits in one chunk
    if total_text <= max_chars:
        return [files]

    # ── Multi-chunk splitting ──────────────────────────────────────────────
    chunks: list[list[dict]] = []
    current: list[dict] = list(image_parts)  # images in first chunk
    current_size = 0

    for part in text_parts:
        text = part.get("text", "")
        part_len = len(text)

        if current_size + part_len <= max_chars:
            # Fits in current chunk
            current.append(part)
            current_size += part_len

        elif part_len <= max_chars:
            # Doesn't fit here, but fits in a fresh chunk
            if current:
                chunks.append(current)
            current = [part]
            current_size = part_len

        else:
            # Oversized single part — split it
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


# ── Streaming helpers ─────────────────────────────────────────────────────────

async def _do_stream_member(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    member: CounselMember,
    user_content: str | list[dict],
    queue: asyncio.Queue,
) -> str:
    """Low-level helper: stream one member request. Returns full response text."""
    full_text = ""
    messages = [
        {"role": "system", "content": member.system},
        {"role": "user", "content": user_content},
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": member.model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
    }

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
                token = chunk["choices"][0]["delta"].get("content") or ""
                if token:
                    full_text += token
                    await queue.put(
                        _sse({"type": "member_token", "role": member.role, "model": member.model, "token": token})
                    )
            except (KeyError, json.JSONDecodeError):
                pass
    return full_text


def _build_user_content(task: str, files: list[dict] | None) -> str | list[dict]:
    """Build multipart user content when files are attached, plain string otherwise."""
    if not files:
        return task
    user_content: list[dict] = [{"type": "text", "text": task}]
    user_content.extend(files)
    return user_content


def _strip_to_text_only(files: list[dict] | None) -> str | None:
    """Extract only text parts from files for fallback (drop image_url parts)."""
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
) -> str:
    """Stream a single chunk with 400-fallback.

    Fallback strategy:
      1. Try with full multipart content (text + images).
      2. On 400 → retry with text-only file parts (images stripped).
      3. On 400 again → retry with plain task text (no files).
    """
    # Attempt 1: full content
    try:
        user_content = _build_user_content(task, chunk_files)
        return await _do_stream_member(client, api_base, api_key, member, user_content, queue)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 400 or not chunk_files:
            raise
        logger.warning("Member %s got 400 with chunk — trying text-only fallback", member.role)

    # Attempt 2: text-only (strip images)
    text_supplement = _strip_to_text_only(chunk_files)
    if text_supplement:
        try:
            return await _do_stream_member(
                client, api_base, api_key, member, f"{task}\n{text_supplement}", queue
            )
        except httpx.HTTPStatusError as exc2:
            if exc2.response.status_code != 400:
                raise
            logger.warning("Member %s still 400 with text fallback — trying plain task", member.role)

    # Attempt 3: plain task only
    return await _do_stream_member(client, api_base, api_key, member, task, queue)


# ── Main member streaming (with chunking) ────────────────────────────────────

async def _stream_member(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    member: CounselMember,
    task: str,
    files: list[dict] | None,
    queue: asyncio.Queue,
) -> str:
    """Stream a single member's response, pushing SSE chunks into *queue*.
    Returns the full accumulated response text.

    Large file content is chunked according to the member's ctx_tokens.
    Each chunk is processed sequentially; a visual separator is streamed
    between chunks so the UI shows progress.
    """
    try:
        max_chars = _max_chunk_chars(member.ctx_tokens or _DEFAULT_CTX_TOKENS)
        file_chunks = _chunk_files(files, max_chars)

        if len(file_chunks) <= 1:
            # ── Single chunk (or no files) — same as before ────────────────
            chunk_text = await _stream_chunk_with_fallback(
                client, api_base, api_key, member, task,
                file_chunks[0] if file_chunks else [],
                queue,
            )
            await queue.put(_sse({"type": "member_done", "role": member.role, "model": member.model}))
            return chunk_text

        # ── Multi-chunk: process each sequentially ─────────────────────────
        full_text = ""
        n = len(file_chunks)
        for i, chunk_parts in enumerate(file_chunks):
            # Stream a visual separator between chunks
            if i > 0:
                sep = f"\n\n━━━ Section {i + 1}/{n} ━━━\n\n"
                full_text += sep
                await queue.put(
                    _sse({"type": "member_token", "role": member.role, "model": member.model, "token": sep})
                )

            chunk_task = f"{task}\n\n[Analysing section {i + 1} of {n} — focus on this section's content]"
            chunk_text = await _stream_chunk_with_fallback(
                client, api_base, api_key, member, chunk_task, chunk_parts, queue,
            )
            full_text += chunk_text

        await queue.put(_sse({"type": "member_done", "role": member.role, "model": member.model}))
        return full_text

    except Exception as exc:
        logger.error("Member %s error: %s", member.role, exc)
        await queue.put(_sse({"type": "member_error", "role": member.role, "error": str(exc)}))
        return ""


# ── Chairperson synthesis ─────────────────────────────────────────────────────

async def _stream_synthesis(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    chairperson: CounselChairperson,
    task: str,
    member_responses: dict[str, str],
    queue: asyncio.Queue,
) -> None:
    """Stream the chairperson's synthesis into *queue*."""
    # Build the synthesis prompt
    responses_block = "\n\n".join(
        f"### {role}\n{text}" for role, text in member_responses.items() if text
    )
    user_content = (
        f"Original task:\n{task}\n\n"
        f"Council responses:\n{responses_block}\n\n"
        f"Please synthesize the above into a consolidated, actionable response."
    )

    messages = [
        {"role": "system", "content": chairperson.system},
        {"role": "user", "content": user_content},
    ]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": chairperson.model,
        "messages": messages,
        "stream": True,
        "temperature": 0.5,
    }

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
                    token = chunk["choices"][0]["delta"].get("content") or ""
                    if token:
                        await queue.put(_sse({"type": "synthesis_token", "token": token}))
                except (KeyError, json.JSONDecodeError):
                    pass
    except Exception as exc:
        logger.error("Chairperson synthesis error: %s", exc)
        await queue.put(_sse({"type": "error", "error": f"Chairperson error: {exc}"}))


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_counsel(
    task: str,
    counsel: CounselConfig,
    api_base: str,
    api_key: str,
    files: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Main SSE generator for a counsel run.

    Yields raw SSE strings suitable for streaming to the browser.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()

    async with httpx.AsyncClient() as client:
        # ── Phase 1: Fan out to all members concurrently ──────────────────
        tasks = [
            asyncio.create_task(
                _stream_member(client, api_base, api_key, member, task, files, queue)
            )
            for member in counsel.members
        ]

        # Drain the queue while tasks run, yielding tokens as they arrive
        pending = set(tasks)
        while pending:
            # Yield any queued items
            while not queue.empty():
                yield queue.get_nowait()

            # Check which tasks finished
            done, pending = await asyncio.wait(pending, timeout=0.05, return_when=asyncio.FIRST_COMPLETED)

        # Drain anything left after all tasks complete
        while not queue.empty():
            yield queue.get_nowait()

        # Collect full member responses
        member_responses = {
            member.role: task_obj.result()
            for member, task_obj in zip(counsel.members, tasks)
        }

        yield _sse({"type": "members_done"})

        # ── Phase 2: Chairperson synthesis ────────────────────────────────
        await _stream_synthesis(
            client, api_base, api_key,
            counsel.chairperson, task, member_responses, queue
        )

        while not queue.empty():
            yield queue.get_nowait()

    yield _sse({"type": "done"})
