"""
main.py — FastAPI server for sn_llama_counsel.

Serves:
  GET  /                        → built Svelte UI (frontend-dist/)
  GET  /api/counsels            → list YAML counsel configs
  GET  /api/models              → proxy to llama-server /v1/models
  POST /api/counsel/run         → SSE: fan-out + synthesis stream
  POST /api/counsel/auto-select → auto-select best council for task

  # Reverse-proxy to llama-server (via mitmweb :8080 → :11434)
  GET|POST /v1/*                → proxy
  GET      /props               → proxy
  GET      /models              → proxy
  GET      /slots               → proxy
  *        /cors-proxy/*        → proxy
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .runner import run_counsel
from .schemas import (
    AutoSelectRequest, AutoSelectResponse, CounselConfig,
    CreateCounselRequest, RunRequest,
)
from .selector import auto_select_counsel

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent.parent  # repo root

def _load_config() -> dict:
    cfg_path = _HERE / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    return {}

_cfg = _load_config()

API_BASE: str = os.environ.get("LLAMA_API_BASE", _cfg.get("api_base", "http://localhost:11434"))
API_KEY: str = os.environ.get("LLAMA_API_KEY", _cfg.get("api_key", "none"))
DEFAULT_MODEL: str = os.environ.get("LLAMA_DEFAULT_MODEL", _cfg.get("default_model", "llama-3.1-8b-instant"))
COUNSELS_DIR: Path = _HERE / "counsels"
FRONTEND_DIR: Path = _HERE / "frontend-dist"

# ── Load counsel configs ────────────────────────────────────────────────────

def _load_counsels() -> list[CounselConfig]:
    configs: list[CounselConfig] = []
    if not COUNSELS_DIR.exists():
        logger.warning("Counsels directory not found: %s", COUNSELS_DIR)
        return configs
    for path in sorted(COUNSELS_DIR.glob("*.yaml")):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            configs.append(CounselConfig(**data))
            logger.info("Loaded counsel: %s", path.name)
        except Exception as exc:
            logger.error("Failed to load %s: %s", path, exc)
    return configs

_counsels: list[CounselConfig] = _load_counsels()

# ── FastAPI app ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="sn_llama_counsel",
    description="AI Counsel for llama.cpp — parallel multi-LLM panel with chairperson synthesis",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/api/counsels", response_model=list[CounselConfig])
async def list_counsels():
    """Return all available counsel configurations."""
    return _counsels


@app.get("/api/models")
async def list_models():
    """Proxy /v1/models from llama-server."""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{API_BASE}/v1/models", headers=headers)
            resp.raise_for_status()
            return JSONResponse(content=resp.json())
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"llama-server unreachable: {exc}") from exc


@app.post("/api/counsel/run")
async def counsel_run(req: RunRequest):
    """
    SSE endpoint — streams the full counsel run.

    Event format:
      data: {"type": "member_token",    "role": "...", "model": "...", "token": "..."}
      data: {"type": "member_done",     "role": "..."}
      data: {"type": "member_error",    "role": "...", "error": "..."}
      data: {"type": "members_done"}
      data: {"type": "synthesis_token", "token": "..."}
      data: {"type": "done"}
      data: {"type": "error",           "error": "..."}
    """
    files_data = (
        [part.model_dump(exclude_none=True) for part in req.files]
        if req.files else None
    )
    return StreamingResponse(
        run_counsel(req.task, req.counsel, API_BASE, API_KEY, files_data),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/counsel/auto-select", response_model=AutoSelectResponse)
async def counsel_auto_select(req: AutoSelectRequest):
    """Use an LLM to pick the best council for the given task."""
    if not _counsels:
        raise HTTPException(status_code=404, detail="No counsels configured")
    selected = await auto_select_counsel(
        req.task, _counsels, DEFAULT_MODEL, API_BASE, API_KEY
    )
    return AutoSelectResponse(counsel=selected)


# ── Counsel creation ─────────────────────────────────────────────────────

_COUNSEL_CREATION_SYSTEM = """\
You are an expert at designing AI advisory panels (called "counsels").
Given a natural language description, design a counsel configuration.
Respond ONLY with a valid JSON object matching this schema:
{
  "name": "lowercase_snake_case_name",
  "description": "one-line description of what this counsel does",
  "chairperson": {
    "model": "<model_name>",
    "system": "system prompt for the chairperson who synthesizes member responses"
  },
  "members": [
    {
      "model": "<model_name>",
      "role": "Role Title",
      "system": "detailed system prompt for this expert role"
    }
  ]
}

Available models (use ONLY these):
- qwq-32b (best for reasoning and deep analysis, slower)
- llama-3.3-70b-versatile (best general-purpose, fast)
- llama-3.1-8b-instant (fastest, good for focused tasks)
- qwen/qwen3-32b (strong reasoning, good at structured output)

Guidelines:
- Design 2-4 members with complementary, non-overlapping perspectives
- Make system prompts specific and detailed (at least 3 sentences each)
- The chairperson should synthesize, not repeat — it merges member insights
- Use qwq-32b or llama-3.3-70b-versatile for the chairperson
- Pick member models based on task complexity

Respond with ONLY valid JSON, no markdown fences, no other text."""


@app.post("/api/counsel/create", response_model=CounselConfig)
async def counsel_create(req: CreateCounselRequest):
    """Use an LLM to design a counsel from a natural language description."""
    global _counsels

    model = DEFAULT_MODEL
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _COUNSEL_CREATION_SYSTEM},
            {"role": "user", "content": f"Design a counsel for: {req.description}"},
        ],
        "stream": False,
        "temperature": 0.7,
        "max_tokens": 2048,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{API_BASE}/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Strip markdown fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            # Also handle trailing fences
            if "```" in content:
                content = content.split("```")[0]

            counsel_data = json.loads(content)
            config = CounselConfig(**counsel_data)

            # Ensure name is a valid filename
            safe_name = re.sub(r"[^a-z0-9_]", "_", config.name.lower().strip())
            if not safe_name:
                safe_name = "custom_counsel"
            counsel_data["name"] = safe_name
            config = CounselConfig(**counsel_data)

            # Handle duplicate names
            filepath = COUNSELS_DIR / f"{safe_name}.yaml"
            if filepath.exists():
                i = 2
                while (COUNSELS_DIR / f"{safe_name}_{i}.yaml").exists():
                    i += 1
                safe_name = f"{safe_name}_{i}"
                counsel_data["name"] = safe_name
                config = CounselConfig(**counsel_data)
                filepath = COUNSELS_DIR / f"{safe_name}.yaml"

            # Save to YAML
            COUNSELS_DIR.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w") as f:
                yaml.dump(counsel_data, f, default_flow_style=False, sort_keys=False)

            logger.info("Created new counsel: %s → %s", safe_name, filepath)

            # Reload counsels list
            _counsels = _load_counsels()

            return config

    except json.JSONDecodeError as exc:
        logger.error("Counsel creation: LLM returned invalid JSON: %s", exc)
        raise HTTPException(
            status_code=422, detail=f"LLM returned invalid JSON: {exc}"
        ) from exc
    except Exception as exc:
        logger.error("Counsel creation failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Failed to create counsel: {exc}"
        ) from exc


# ── Reverse proxy to llama-server ──────────────────────────────────────────
# The Svelte UI calls /props, /v1/*, /models, /slots on the same origin.
# We proxy these through to llama-server (via mitmweb at API_BASE) so the
# existing chat UI works and all calls are visible in the mitmweb inspector.

_proxy_client = httpx.AsyncClient(timeout=300.0)  # long timeout for streaming


async def _proxy_request(request: Request, path: str) -> StreamingResponse:
    """Forward an incoming request to llama-server and stream the response back."""
    url = f"{API_BASE}/{path}"
    headers = dict(request.headers)
    # Remove hop-by-hop headers
    for h in ("host", "transfer-encoding", "connection"):
        headers.pop(h, None)
    if API_KEY and API_KEY != "none":
        headers["authorization"] = f"Bearer {API_KEY}"

    body = await request.body()

    req = _proxy_client.build_request(
        method=request.method,
        url=url,
        headers=headers,
        content=body if body else None,
        params=request.query_params,
    )

    try:
        resp = await _proxy_client.send(req, stream=True)
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"llama-server unreachable at {API_BASE}: {exc}") from exc

    resp_headers = dict(resp.headers)
    for h in ("transfer-encoding", "content-encoding", "content-length"):
        resp_headers.pop(h, None)

    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@app.api_route("/props", methods=["GET"])
async def proxy_props(request: Request):
    """Proxy /props to llama-server; return a fallback if unreachable."""
    try:
        resp = await _proxy_request(request, "props")
        # Check if the proxied response is an error (mitmweb/upstream failure)
        if resp.status_code >= 500:
            raise Exception("upstream error")
        return resp
    except Exception:
        # llama-server not running — return minimal router-mode props
        # so the Svelte UI doesn't show "Server unavailable" and counsel still works
        return JSONResponse({
            "role": "router",
            "total_slots": 0,
            "model_path": "",
            "chat_template": "",
            "default_generation_settings": {
                "n_ctx": 4096,
                "params": {},
            },
        })


@app.api_route("/models", methods=["GET"])
async def proxy_models(request: Request):
    return await _proxy_request(request, "models")


@app.api_route("/slots", methods=["GET"])
async def proxy_slots(request: Request):
    return await _proxy_request(request, "slots")


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_v1(request: Request, path: str):
    return await _proxy_request(request, f"v1/{path}")


@app.api_route("/cors-proxy/{path:path}", methods=["GET", "POST"])
async def proxy_cors(request: Request, path: str):
    return await _proxy_request(request, f"cors-proxy/{path}")


# ── Static file serving (Svelte UI) ────────────────────────────────────────
# Mounted last so API routes take priority.

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    logger.info("Serving frontend from %s", FRONTEND_DIR)
else:
    logger.warning(
        "Frontend dist not found at %s — run 'cd frontend && npm run build' first",
        FRONTEND_DIR,
    )

    @app.get("/")
    async def no_frontend():
        return JSONResponse(
            {"error": "Frontend not built. Run: cd frontend && npm install && npm run build"},
            status_code=503,
        )


# ── CLI entrypoint ──────────────────────────────────────────────────────────

def main():
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="sn_llama_counsel server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--reload", action="store_true", help="Dev mode auto-reload")
    args = parser.parse_args()

    uvicorn.run(
        "sn_llama_counsel.backend.main:app" if not args.reload else "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
