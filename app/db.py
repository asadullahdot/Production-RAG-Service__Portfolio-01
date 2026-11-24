"""SQLite: documents registry (core) and users table (B1) — PLAN.md §3, §4.1.

One connection, WAL mode, ``busy_timeout``, and one lock serialising access (single worker).
All methods are synchronous; async callers use ``run_in_threadpool``.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    file_type   TEXT NOT NULL,
    num_pages   INTEGER NOT NULL,
    num_chunks  INTEGER NOT NULL,
    file_hash   TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_owner_hash ON documents(owner_id, file_hash);
CREATE INDEX IF NOT EXISTS ix_documents_owner ON documents(owner_id);
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str, timeout_s: float = 10.0):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=timeout_s, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute(f"PRAGMA busy_timeout = {int(timeout_s * 1000)}")
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---- helpers
    def _run(self, sql: str, params: tuple = (), write: bool = False):
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            if write:
                self._conn.commit()
            return rows

    def ping(self) -> bool:
        try:
            self._run("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- documents registry
    def add_document(self, *, doc_id: str, owner_id: str, file_name: str, file_type: str,
                     num_pages: int, num_chunks: int, file_hash: str, uploaded_at: str | None = None) -> dict:
        uploaded_at = uploaded_at or now_iso()
        self._run(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, owner_id, file_name, file_type, num_pages, num_chunks, file_hash, uploaded_at),
            write=True,
        )
        return self.get_document(owner_id, doc_id)  # type: ignore[return-value]

    def get_document(self, owner_id: str, doc_id: str) -> dict | None:
        rows = self._run("SELECT * FROM documents WHERE owner_id=? AND doc_id=?", (owner_id, doc_id))
        return dict(rows[0]) if rows else None

    def find_by_hash(self, owner_id: str, file_hash: str) -> dict | None:
        rows = self._run("SELECT * FROM documents WHERE owner_id=? AND file_hash=?", (owner_id, file_hash))
        return dict(rows[0]) if rows else None

    def list_documents(self, owner_id: str) -> list[dict]:
        rows = self._run("SELECT * FROM documents WHERE owner_id=? ORDER BY uploaded_at DESC, doc_id", (owner_id,))
        return [dict(r) for r in rows]

    def doc_ids(self, owner_id: str) -> set[str]:
        return {r["doc_id"] for r in self._run("SELECT doc_id FROM documents WHERE owner_id=?", (owner_id,))}

    def delete_document(self, owner_id: str, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM documents WHERE owner_id=? AND doc_id=?", (owner_id, doc_id))
            self._conn.commit()
            return cur.rowcount > 0

    # ---- users (B1)
    def create_user(self, email: str, password_hash: str) -> dict:
        user_id = uuid.uuid4().hex
        self._run("INSERT INTO users VALUES (?,?,?,?)", (user_id, email.lower(), password_hash, now_iso()), write=True)
        return {"user_id": user_id, "email": email.lower()}

    def get_user_by_email(self, email: str) -> dict | None:
        rows = self._run("SELECT * FROM users WHERE email=?", (email.lower(),))
        return dict(rows[0]) if rows else None

    def get_user(self, user_id: str) -> dict | None:
        rows = self._run("SELECT * FROM users WHERE user_id=?", (user_id,))
        return dict(rows[0]) if rows else None

    def count_users(self) -> int:
        return int(self._run("SELECT COUNT(*) AS n FROM users")[0]["n"])
