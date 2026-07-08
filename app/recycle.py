"""A ledger of extensions recycled (freed) from prior IVR use.

Deleting an IVR frees its number in the dialplan, but leaves no record of what
the number was for. Recycling instead *logs the previous use* here before the
dialplan is freed, so the pool number can be handed back out (via
``extensions.allocate``) with an audit trail of its history.

Stored as a JSON array in the app's writable state dir (RECYCLE_LOG_FILE),
newest entry last. No FusionPBX changes — this is app-side bookkeeping only.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from app.config import settings


def _path() -> str:
    return settings.recycle_log_file


def entries() -> list[dict]:
    """All ledger entries, newest first."""
    try:
        with open(_path()) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return list(reversed([e for e in data if isinstance(e, dict)]))


def _write(items: list[dict]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(items, f, indent=2)


def record(extension: int, *, kind: str, name: str, actor: str) -> dict:
    """Log the previous use of an extension before it is freed for reuse."""
    entry = {
        "extension": int(extension),
        "kind": kind,                 # e.g. "ivr"
        "previous_name": name or "",
        "recycled_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "recycled_by": actor or "unknown",
    }
    stored = list(reversed(entries()))  # back to on-disk (oldest-first) order
    stored.append(entry)
    _write(stored)
    return entry


def remove(extension: int, recycled_at: str) -> bool:
    """Delete a single ledger entry, identified by its extension + timestamp.
    Returns True if an entry was removed. App-side only — does not touch FusionPBX."""
    stored = list(reversed(entries()))  # oldest-first on-disk order
    kept = [
        e for e in stored
        if not (e.get("extension") == int(extension) and e.get("recycled_at") == recycled_at)
    ]
    if len(kept) == len(stored):
        return False
    _write(kept)
    return True
