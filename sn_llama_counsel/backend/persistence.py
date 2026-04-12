"""
persistence.py — SQLite-backed session & run history for counsel deliberations.

Schema:
  sessions       (id, title, created_at, updated_at)
  runs           (id, session_id, parent_run_id, task, counsel_snapshot,
                  synthesis, status, created_at, finished_at)
  member_responses (run_id, role, model, content, error)
  usage          (run_id, role, prompt_tokens, completion_tokens)

The store is used by the counsel runner to persist each run at completion
(or on cancellation) and by the API layer to list/read history for the UI.

All DB work is stdlib sqlite3 wrapped in asyncio.to_thread so the FastAPI
event loop never blocks on disk I/O. Reads/writes are tiny and infrequent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_ms() -> int:
    return int(time.time() * 1000)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL,
    parent_run_id     TEXT,
    task              TEXT NOT NULL,
    counsel_snapshot  TEXT NOT NULL,
    synthesis         TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'running',
    created_at        INTEGER NOT NULL,
    finished_at       INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_parent  ON runs(parent_run_id);

CREATE TABLE IF NOT EXISTS member_responses (
    run_id   TEXT NOT NULL,
    role     TEXT NOT NULL,
    model    TEXT NOT NULL,
    content  TEXT NOT NULL DEFAULT '',
    error    TEXT,
    PRIMARY KEY (run_id, role),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS usage (
    run_id            TEXT NOT NULL,
    role              TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, role),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
"""


class PersistenceStore:
    """Thin async wrapper around a sqlite3 connection.

    Not thread-safe for a single connection — we serialize access via an
    asyncio lock. For the modest write rate of a counsel run (a handful of
    rows per run) this is completely adequate and keeps deps at zero.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        logger.info("Counsel persistence ready: %s", db_path)

    # ── helpers ─────────────────────────────────────────────────────────────

    async def _exec(self, fn, *args):
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    def _sync_close(self):
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            pass

    async def close(self):
        await asyncio.to_thread(self._sync_close)

    # ── sessions ────────────────────────────────────────────────────────────

    def _sync_create_session(self, title: str) -> dict[str, Any]:
        sid = _new_id()
        now = _now_ms()
        self._conn.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (sid, title, now, now),
        )
        return {"id": sid, "title": title, "created_at": now, "updated_at": now}

    async def create_session(self, title: str = "") -> dict[str, Any]:
        return await self._exec(self._sync_create_session, title)

    def _sync_list_sessions(self) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM runs r WHERE r.session_id = s.id) AS run_count
            FROM sessions s
            ORDER BY s.updated_at DESC
            """
        )
        return [dict(row) for row in cur.fetchall()]

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await self._exec(self._sync_list_sessions)

    def _sync_delete_session(self, session_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0

    async def delete_session(self, session_id: str) -> bool:
        return await self._exec(self._sync_delete_session, session_id)

    def _sync_touch_session(self, session_id: str, title_if_empty: str = ""):
        now = _now_ms()
        # Create if missing (idempotent — clients may pass a client-chosen id)
        self._conn.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (session_id, title_if_empty, now, now),
        )
        # Backfill title if we created it with empty title earlier
        if title_if_empty:
            self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
                (title_if_empty, session_id),
            )

    async def touch_session(self, session_id: str, title_if_empty: str = ""):
        await self._exec(self._sync_touch_session, session_id, title_if_empty)

    # ── runs ────────────────────────────────────────────────────────────────

    def _sync_create_run(
        self,
        session_id: str,
        task: str,
        counsel_snapshot: dict,
        parent_run_id: Optional[str],
    ) -> str:
        rid = _new_id()
        now = _now_ms()
        self._conn.execute(
            """
            INSERT INTO runs (id, session_id, parent_run_id, task, counsel_snapshot,
                              status, created_at)
            VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (rid, session_id, parent_run_id, task, json.dumps(counsel_snapshot), now),
        )
        self._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        return rid

    async def create_run(
        self,
        session_id: str,
        task: str,
        counsel_snapshot: dict,
        parent_run_id: Optional[str] = None,
    ) -> str:
        return await self._exec(
            self._sync_create_run, session_id, task, counsel_snapshot, parent_run_id
        )

    def _sync_finalize_run(
        self,
        run_id: str,
        synthesis: str,
        status: str,
        member_responses: dict[str, dict[str, Any]],
        usage: dict[str, dict[str, int]],
    ):
        now = _now_ms()
        self._conn.execute(
            "UPDATE runs SET synthesis = ?, status = ?, finished_at = ? WHERE id = ?",
            (synthesis, status, now, run_id),
        )
        for role, info in member_responses.items():
            self._conn.execute(
                """
                INSERT INTO member_responses (run_id, role, model, content, error)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, role) DO UPDATE SET
                    content = excluded.content, error = excluded.error
                """,
                (
                    run_id,
                    role,
                    info.get("model", ""),
                    info.get("content", ""),
                    info.get("error"),
                ),
            )
        for role, u in usage.items():
            self._conn.execute(
                """
                INSERT INTO usage (run_id, role, prompt_tokens, completion_tokens)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, role) DO UPDATE SET
                    prompt_tokens = excluded.prompt_tokens,
                    completion_tokens = excluded.completion_tokens
                """,
                (run_id, role, int(u.get("prompt", 0)), int(u.get("completion", 0))),
            )

    async def finalize_run(
        self,
        run_id: str,
        synthesis: str,
        status: str,
        member_responses: dict[str, dict[str, Any]],
        usage: dict[str, dict[str, int]],
    ):
        await self._exec(
            self._sync_finalize_run,
            run_id,
            synthesis,
            status,
            member_responses,
            usage,
        )

    def _sync_list_runs(self, session_id: str) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT id, session_id, parent_run_id, task, synthesis, status,
                   created_at, finished_at, counsel_snapshot
            FROM runs
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        runs = []
        for row in cur.fetchall():
            d = dict(row)
            try:
                d["counsel_snapshot"] = json.loads(d["counsel_snapshot"])
            except Exception:
                d["counsel_snapshot"] = {}
            runs.append(d)
        return runs

    async def list_runs(self, session_id: str) -> list[dict[str, Any]]:
        return await self._exec(self._sync_list_runs, session_id)

    def _sync_get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT id, session_id, parent_run_id, task, synthesis, status,
                   created_at, finished_at, counsel_snapshot
            FROM runs WHERE id = ?
            """,
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["counsel_snapshot"] = json.loads(d["counsel_snapshot"])
        except Exception:
            d["counsel_snapshot"] = {}
        mr_cur = self._conn.execute(
            "SELECT role, model, content, error FROM member_responses WHERE run_id = ?",
            (run_id,),
        )
        d["members"] = [dict(r) for r in mr_cur.fetchall()]
        u_cur = self._conn.execute(
            "SELECT role, prompt_tokens, completion_tokens FROM usage WHERE run_id = ?",
            (run_id,),
        )
        d["usage"] = [dict(r) for r in u_cur.fetchall()]
        return d

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        return await self._exec(self._sync_get_run, run_id)

    async def get_run_chain(self, run_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Walk parent_run_id pointers back up to the root, returning runs in
        chronological order (root first). Used to assemble follow-up context.
        """
        chain: list[dict[str, Any]] = []
        current: Optional[str] = run_id
        seen: set[str] = set()
        while current and current not in seen and len(chain) < limit:
            seen.add(current)
            run = await self.get_run(current)
            if not run:
                break
            chain.append(run)
            current = run.get("parent_run_id")
        chain.reverse()
        return chain
