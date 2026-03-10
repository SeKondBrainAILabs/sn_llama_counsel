"""
main.py — FastAPI server for sn_llama_counsel.

Serves:
  GET  /                        → built Svelte UI (frontend-dist/)
  GET  /api/counsels            → list YAML counsel configs
  GET  /api/models              → proxy to llama-server /v1/models
  POST /api/counsel/run         → SSE: fan-out + synthesis stream
  POST /api/counsel/auto-select → auto-select best council for task
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .runner import run_counsel
from .schemas import AutoSelectRequest, AutoSelectResponse, CounselConfig, RunRequest
from .selector import auto_select_counsel

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent.parent.parent  # repo root

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
    return StreamingResponse(
        run_counsel(req.task, req.counsel, API_BASE, API_KEY),
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
