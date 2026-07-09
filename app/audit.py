"""Append-only, tamper-evident audit log (hash-chained).

Every entry stores the SHA-256 of the previous entry's hash combined with its
own canonical payload. Deleting or modifying any earlier entry therefore breaks
the chain and is detectable via :func:`verify`. The log lives in a small SQLite
DB in the app state dir (like the local users DB) and is readable only by
admins (the ``view_audit`` permission — see :mod:`app.auth.authz`).

Design notes:
  - Writes never raise to the caller: a broken audit backend must not break the
    action being audited. Failures are logged instead (see :func:`record`).
  - The hash covers the *stored* representation of every field, so
    :func:`verify` recomputes purely from table columns with no round-tripping.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from app.config import settings

log = logging.getLogger("fpbx_ivr_manager")

# The prev_hash of the very first entry. 64 hex zeros == "no previous entry".
GENESIS = "0" * 64


@contextmanager
def _conn():
    path = settings.audit_db
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit; we use explicit txns
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit ("
            "  seq       INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ts        TEXT NOT NULL,"   # ISO-8601 UTC
            "  actor     TEXT NOT NULL,"   # who did it (username/email)
            "  action    TEXT NOT NULL,"   # e.g. 'user.delete'
            "  target    TEXT,"            # object acted on (id/name), optional
            "  details   TEXT,"            # JSON string, optional
            "  prev_hash TEXT NOT NULL,"   # hash of the previous entry (or GENESIS)
            "  hash      TEXT NOT NULL"    # this entry's chained hash
            ")"
        )
        yield conn
    finally:
        conn.close()


def _entry_hash(prev_hash: str, ts: str, actor: str, action: str,
                target: str | None, details_json: str | None) -> str:
    """Deterministic hash over the previous hash and this entry's stored fields."""
    payload = json.dumps(
        {"ts": ts, "actor": actor, "action": action,
         "target": target, "details": details_json},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256((prev_hash + "\n" + payload).encode("utf-8")).hexdigest()


def _details_json(details: dict | None) -> str | None:
    if not details:
        return None
    return json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record(actor: str, action: str, target: str | None = None,
           details: dict | None = None) -> None:
    """Append one entry, chained to the current tail. Never raises."""
    try:
        ts = datetime.now(UTC).isoformat()
        dj = _details_json(details)
        with _conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT hash FROM audit ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                prev = row["hash"] if row else GENESIS
                h = _entry_hash(prev, ts, actor or "?", action, target, dj)
                conn.execute(
                    "INSERT INTO audit (ts, actor, action, target, details, prev_hash, hash)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (ts, actor or "?", action, target, dj, prev, h),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    except Exception:  # pragma: no cover - audit must never break the request
        log.exception("audit record failed for action=%s", action)


def count() -> int:
    with _conn() as conn:
        return int(conn.execute("SELECT count(*) AS n FROM audit").fetchone()["n"])


def entries(limit: int = 200, offset: int = 0) -> list[dict]:
    """Most recent entries first."""
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT seq, ts, actor, action, target, details, prev_hash, hash "
            "FROM audit ORDER BY seq DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def verify() -> dict:
    """Recompute the chain from the beginning. Returns
    ``{"ok": bool, "count": int, "bad_seq": int | None}`` where ``bad_seq`` is
    the first entry whose stored hash or prev-link does not match."""
    prev = GENESIS
    n = 0
    with _conn() as conn:
        rows = conn.execute(
            "SELECT seq, ts, actor, action, target, details, prev_hash, hash "
            "FROM audit ORDER BY seq ASC"
        ).fetchall()
    for r in rows:
        n += 1
        expected = _entry_hash(r["prev_hash"], r["ts"], r["actor"], r["action"],
                               r["target"], r["details"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"ok": False, "count": n, "bad_seq": r["seq"]}
        prev = r["hash"]
    return {"ok": True, "count": n, "bad_seq": None}
