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

import contextvars
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime

from app.config import settings

log = logging.getLogger("fpbx_ivr_manager")

# The prev_hash of the very first entry. 64 hex zeros == "no previous entry".
GENESIS = "0" * 64

# Serialize appends *within this process* so two concurrent events can never
# read the same chain tail and fork the hash chain. Across processes (e.g. a
# multi-worker deployment) SQLite's write lock + busy_timeout + the retry loop
# in record() serialize the append instead — BEGIN IMMEDIATE means the second
# writer only reads the tail after the first has committed.
_write_lock = threading.Lock()

# The real client IP for the current request, resolved through proxy layers by
# ClientIPMiddleware. record() stamps it onto each entry unless an explicit ip
# is passed. A ContextVar so the value also flows into the threadpool where the
# sync route handlers (and their audit hooks) run.
_client_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "audit_client_ip", default=None
)


def set_client_ip(ip: str | None) -> None:
    _client_ip.set(ip or None)


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
        # wait (don't fail) if another writer/process holds the write lock
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit ("
            "  seq       INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  ts        TEXT NOT NULL,"   # ISO-8601 UTC
            "  actor     TEXT NOT NULL,"   # who did it (username/email)
            "  action    TEXT NOT NULL,"   # e.g. 'user.delete'
            "  target    TEXT,"            # object acted on (id/name), optional
            "  details   TEXT,"            # JSON string, optional
            "  ip        TEXT,"            # source IP (proxy-aware), optional
            "  prev_hash TEXT NOT NULL,"   # hash of the previous entry (or GENESIS)
            "  hash      TEXT NOT NULL"    # this entry's chained hash
            ")"
        )
        # migrate DBs created before the ip column existed (older entries keep
        # ip NULL; their hashes were computed without it, so they still verify)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit)")}
        if "ip" not in cols:
            conn.execute("ALTER TABLE audit ADD COLUMN ip TEXT")
        yield conn
    finally:
        conn.close()


def _entry_hash(prev_hash: str, ts: str, actor: str, action: str,
                target: str | None, details_json: str | None,
                ip: str | None = None) -> str:
    """Deterministic hash over the previous hash and this entry's stored fields.

    ``ip`` is added to the payload only when present, so entries written before
    the ip column existed (ip NULL) hash exactly as they originally did and the
    chain still verifies."""
    fields = {"ts": ts, "actor": actor, "action": action,
              "target": target, "details": details_json}
    if ip:
        fields["ip"] = ip
    payload = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256((prev_hash + "\n" + payload).encode("utf-8")).hexdigest()


def _details_json(details: dict | None) -> str | None:
    if not details:
        return None
    return json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record(actor: str, action: str, target: str | None = None,
           details: dict | None = None, ip: str | None = None) -> None:
    """Append one entry, chained to the current tail. Concurrency-safe and never
    raises to the caller (a broken audit backend must not break the action).

    ``ip`` defaults to the current request's proxy-resolved client IP."""
    ts = datetime.now(UTC).isoformat()
    dj = _details_json(details)
    if ip is None:
        ip = _client_ip.get()
    try:
        # In-process lock: two concurrent events serialize here, so neither can
        # read a stale chain tail and fork the hash chain.
        with _write_lock:
            _append(ts, actor or "?", action, target, dj, ip)
    except Exception:  # pragma: no cover - audit must never break the request
        log.exception("audit record failed for action=%s", action)


def _append(ts: str, actor: str, action: str, target: str | None,
            dj: str | None, ip: str | None, retries: int = 5) -> None:
    """Read the tail and insert the chained entry atomically. BEGIN IMMEDIATE
    takes the write lock up front, so across processes a second writer only sees
    the tail after the first commits; on a transient lock we back off and retry."""
    for attempt in range(retries):
        try:
            with _conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT hash FROM audit ORDER BY seq DESC LIMIT 1"
                    ).fetchone()
                    prev = row["hash"] if row else GENESIS
                    h = _entry_hash(prev, ts, actor, action, target, dj, ip)
                    conn.execute(
                        "INSERT INTO audit (ts, actor, action, target, details, ip, prev_hash, hash)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (ts, actor, action, target, dj, ip, prev, h),
                    )
                    conn.execute("COMMIT")
                    return
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                time.sleep(0.05 * (attempt + 1))
                continue
            raise


def count() -> int:
    with _conn() as conn:
        return int(conn.execute("SELECT count(*) AS n FROM audit").fetchone()["n"])


def entries(limit: int = 200, offset: int = 0) -> list[dict]:
    """Most recent entries first."""
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT seq, ts, actor, action, target, details, ip, prev_hash, hash "
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
            "SELECT seq, ts, actor, action, target, details, ip, prev_hash, hash "
            "FROM audit ORDER BY seq ASC"
        ).fetchall()
    for r in rows:
        n += 1
        expected = _entry_hash(r["prev_hash"], r["ts"], r["actor"], r["action"],
                               r["target"], r["details"], r["ip"])
        if r["prev_hash"] != prev or r["hash"] != expected:
            return {"ok": False, "count": n, "bad_seq": r["seq"]}
        prev = r["hash"]
    return {"ok": True, "count": n, "bad_seq": None}
