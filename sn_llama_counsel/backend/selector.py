"""
selector.py — Auto-select council via chairperson LLM.

Given a task description and the list of available councils,
the chairperson picks the most appropriate one.
"""
from __future__ import annotations

import json
import logging

import httpx

from .schemas import CounselConfig

logger = logging.getLogger(__name__)


AUTO_SELECT_SYSTEM = """You are an expert council coordinator.
Given a task or question and a list of available councils, select the most appropriate council.
Respond ONLY with a valid JSON object: {"council_name": "<name>"}
Do not include any other text."""


async def auto_select_counsel(
    task: str,
    counsels: list[CounselConfig],
    default_model: str,
    api_base: str,
    api_key: str,
) -> CounselConfig:
    """Use an LLM to pick the best council from *counsels* for *task*.
    Falls back to the first council on any error."""
    if not counsels:
        raise ValueError("No counsels available")

    councils_desc = "\n".join(
        f'- {c.name}: {c.description} (members: {", ".join(m.role for m in c.members)})'
        for c in counsels
    )

    user_msg = (
        f"Task:\n{task}\n\n"
        f"Available councils:\n{councils_desc}\n\n"
        "Which council name is most appropriate? Reply with JSON only."
    )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": default_model,
        "messages": [
            {"role": "system", "content": AUTO_SELECT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 64,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_base}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Parse JSON — strip markdown fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            result = json.loads(content)
            name = result["council_name"]

            found = next((c for c in counsels if c.name == name), None)
            if found:
                logger.info("Auto-selected council: %s", name)
                return found
            logger.warning("Auto-select returned unknown council %r; using default", name)
    except Exception as exc:
        logger.error("Auto-select failed: %s; using default council", exc)

    return counsels[0]
