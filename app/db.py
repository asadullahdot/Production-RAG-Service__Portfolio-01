"""SQLite: documents registry (core) and users table (B1) — PLAN.md §3, §4.1.

One connection, WAL mode, ``busy_timeout``, and one lock serialising access (single worker).
All methods are synchronous; async callers use ``run_in_threadpool``.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.errors import QuotaExceeded

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    file_type   TEXT NOT NULL,
    num_pages   INTEGER NOT NULL,
    num_chunks  INTEGER NOT NULL,
    file_hash   TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    flagged     INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_owner_hash ON documents(owner_id, file_hash);
CREATE INDEX IF NOT EXISTS ix_documents_owner ON documents(owner_id);
-- Model tokens spent per user and UTC day, for the daily limit. A table of its own (not the B3 traces): traces are pruned and can
-- be switched off, a limit must not depend on them.
CREATE TABLE IF NOT EXISTS token_usage (
    owner_id      TEXT NOT NULL,
    day           TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_id, day)
);
CREATE TABLE IF NOT EXISTS requests (
    request_id       TEXT PRIMARY KEY,
    ts               TEXT NOT NULL,
    owner_id         TEXT,
    route            TEXT NOT NULL,
    method           TEXT NOT NULL,
    status           INTEGER NOT NULL,
    latency_ms       REAL NOT NULL,
    outcome          TEXT NOT NULL,
    reason           TEXT,
    error_code       TEXT,
    num_retrieved    INTEGER,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    embed_tokens_est INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_requests_owner_ts ON requests(owner_id, ts);
CREATE TABLE IF NOT EXISTS spans (
    request_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    depth       INTEGER NOT NULL,
    offset_ms   REAL NOT NULL,
    duration_ms REAL NOT NULL,
    status      TEXT NOT NULL,
    attrs       TEXT NOT NULL,
    PRIMARY KEY (request_id, seq)
);
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_day() -> str:
    """The current day in UTC, the key of the daily token count (tests replace this function to simulate the next day)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def next_utc_midnight() -> datetime:
    """When the daily token count starts again."""
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


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
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(documents)")}
            if "flagged" not in columns:  # a database created before B6
                self._conn.execute("ALTER TABLE documents ADD COLUMN flagged INTEGER NOT NULL DEFAULT 0")
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
                     num_pages: int, num_chunks: int, file_hash: str, uploaded_at: str | None = None,
                     flagged: bool = False, max_docs: int = 0, max_chunks: int = 0) -> dict:
        """Insert a registry row. ``max_docs`` / ``max_chunks`` are the owner's limits (0 = none); raises QuotaExceeded if this
        document would exceed one of them."""
        uploaded_at = uploaded_at or now_iso()
        with self._lock:
            # The final, atomic check of the per-user limits. IngestionService also checks early (so nothing is embedded for a
            # document that cannot be kept), but two uploads can pass that early check at the same moment. Counting and inserting
            # under the same lock means they cannot both be stored. The caller deletes the document's vectors when this raises.
            docs, chunks = self.usage_counts(owner_id)
            if max_docs and docs >= max_docs:
                raise QuotaExceeded(f"You have reached the limit of {max_docs} document{'' if max_docs == 1 else 's'}. Delete one to upload another.",
                                    {"kind": "documents", "limit": max_docs, "used": docs})
            if max_chunks and chunks + num_chunks > max_chunks:
                raise QuotaExceeded(f"This document would take you over your limit of {max_chunks:,} chunks ({chunks:,} used). Delete a document first.",
                                    {"kind": "chunks", "limit": max_chunks, "used": chunks})
            self._run(
                "INSERT INTO documents (doc_id, owner_id, file_name, file_type, num_pages, num_chunks, file_hash, uploaded_at, flagged)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, owner_id, file_name, file_type, num_pages, num_chunks, file_hash, uploaded_at, int(flagged)),
                write=True,
            )
        return self.get_document(owner_id, doc_id)  # type: ignore[return-value]

    def usage_counts(self, owner_id: str) -> tuple[int, int]:
        """(documents, chunks) the owner has stored: what the storage limits are measured against."""
        row = self._run("SELECT COUNT(*) AS n, COALESCE(SUM(num_chunks), 0) AS c FROM documents WHERE owner_id=?", (owner_id,))[0]
        return int(row["n"]), int(row["c"])

    # ---- token usage per UTC day (for the daily limit)
    def add_usage(self, owner_id: str, input_tokens: int, output_tokens: int) -> None:
        """Add tokens to the owner's count for today (one row per owner and day, created on first use)."""
        self._run("INSERT INTO token_usage (owner_id, day, input_tokens, output_tokens) VALUES (?,?,?,?) "
                  "ON CONFLICT(owner_id, day) DO UPDATE SET input_tokens = input_tokens + excluded.input_tokens, "
                  "output_tokens = output_tokens + excluded.output_tokens",
                  (owner_id, utc_day(), input_tokens, output_tokens), write=True)

    def tokens_today(self, owner_id: str) -> int:
        """Input + output tokens the owner has spent since 00:00 UTC (0 if none)."""
        rows = self._run("SELECT input_tokens + output_tokens AS t FROM token_usage WHERE owner_id=? AND day=?", (owner_id, utc_day()))
        return int(rows[0]["t"]) if rows else 0

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

    # ---- traces and monitoring (B3)
    _REQUEST_COLUMNS = ("request_id", "ts", "owner_id", "route", "method", "status", "latency_ms", "outcome", "reason",
                        "error_code", "num_retrieved", "input_tokens", "output_tokens", "embed_tokens_est", "cost_usd")

    def add_trace(self, row: dict, spans: list[dict]) -> None:
        cols = self._REQUEST_COLUMNS
        with self._lock:
            existing = self._conn.execute("SELECT owner_id FROM requests WHERE request_id=?", (row["request_id"],)).fetchone()
            if existing is not None and existing["owner_id"] != row.get("owner_id"):
                return  # a client-chosen X-Request-ID that belongs to someone else's trace: keep theirs
            self._conn.execute("DELETE FROM spans WHERE request_id=?", (row["request_id"],))  # a client may reuse a request id
            self._conn.execute(f"INSERT OR REPLACE INTO requests ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               tuple(row.get(c) for c in cols))
            self._conn.executemany(
                "INSERT INTO spans (request_id, seq, name, depth, offset_ms, duration_ms, status, attrs) VALUES (?,?,?,?,?,?,?,?)",
                [(row["request_id"], s["seq"], s["name"], s["depth"], s["offset_ms"], s["duration_ms"], s["status"], s["attrs"])
                 for s in spans])
            self._conn.commit()

    def prune_traces(self, keep: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM requests WHERE request_id IN "
                               "(SELECT request_id FROM requests ORDER BY ts DESC LIMIT -1 OFFSET ?)", (keep,))
            self._conn.execute("DELETE FROM spans WHERE request_id NOT IN (SELECT request_id FROM requests)")
            self._conn.commit()

    def trace_requests(self, owner_id: str, since: str | None = None, limit: int | None = None) -> list[dict]:
        """The owner's traced requests, newest first."""
        sql = "SELECT * FROM requests WHERE owner_id=?" + (" AND ts>=?" if since else "") + " ORDER BY ts DESC"
        params: tuple = (owner_id, since) if since else (owner_id,)
        if limit:
            sql += " LIMIT ?"
            params += (limit,)
        return [dict(r) for r in self._run(sql, params)]

    def trace_step_rows(self, owner_id: str, since: str | None = None) -> list[dict]:
        """Step spans (depth 1) of the owner's requests, for per-step latency."""
        sql = ("SELECT r.route AS route, s.name AS name, s.duration_ms AS duration_ms, s.offset_ms AS offset_ms, s.status AS status "
               "FROM spans s JOIN requests r ON r.request_id = s.request_id WHERE r.owner_id=? AND s.depth=1"
               + (" AND r.ts>=?" if since else ""))
        return [dict(r) for r in self._run(sql, (owner_id, since) if since else (owner_id,))]

    def get_trace(self, owner_id: str, request_id: str) -> tuple[dict, list[dict]] | None:
        rows = self._run("SELECT * FROM requests WHERE owner_id=? AND request_id=?", (owner_id, request_id))
        if not rows:
            return None
        spans = self._run("SELECT * FROM spans WHERE request_id=? ORDER BY seq", (request_id,))
        return dict(rows[0]), [dict(s) for s in spans]

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
