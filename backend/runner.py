"""
runner.py — Parallel LLM fan-out and chairperson synthesis.

Phase 1: All council members are queried concurrently with asyncio.
         SSE events stream each member's tokens as they arrive.

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


def _sse(event: dict[str, Any]) -> str:
    """Format a dict as a Server-Sent Events data line."""
    return f"data: {json.dumps(event)}\n\n"


async def _stream_member(
    client: httpx.AsyncClient,
    api_base: str,
    api_key: str,
    member: CounselMember,
    task: str,
    queue: asyncio.Queue,
) -> str:
    """Stream a single member's response, pushing SSE chunks into *queue*.
    Returns the full accumulated response text."""
    full_text = ""
    try:
        messages = [
            {"role": "system", "content": member.system},
            {"role": "user", "content": task},
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

        await queue.put(_sse({"type": "member_done", "role": member.role, "model": member.model}))
        return full_text

    except Exception as exc:
        logger.error("Member %s error: %s", member.role, exc)
        await queue.put(_sse({"type": "member_error", "role": member.role, "error": str(exc)}))
        return ""


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


async def run_counsel(
    task: str,
    counsel: CounselConfig,
    api_base: str,
    api_key: str,
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
                _stream_member(client, api_base, api_key, member, task, queue)
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
