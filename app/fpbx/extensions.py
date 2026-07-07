"""Allocate extensions from the managed pool 9550-9599.

An extension is considered 'in use' if any dialplan (our time conditions) or
extension record references it in the domain. We track ours by a naming
convention: dialplan name 'closure_<ext>'.
"""
from __future__ import annotations

from app.config import settings
from app.fpbx.db import cursor, domain_uuid

POOL = range(settings.ext_pool_start, settings.ext_pool_end + 1)


def used_extensions() -> set[int]:
    """Every number in the pool that is occupied by *anything* — our closures,
    foreign dialplans/time conditions, or real extensions. Auto-allocation only
    ever hands out a number that is completely free, so we never step on a
    construct we did not create."""
    d = domain_uuid()
    used: set[int] = set()
    with cursor() as cur:
        # ANY dialplan on a pool number (ours or foreign), by number
        cur.execute("SELECT dialplan_number FROM v_dialplans WHERE domain_uuid = %s", (d,))
        for row in cur.fetchall():
            _add_if_int(used, row["dialplan_number"])
        # real extensions, so we never collide with a phone
        cur.execute("SELECT extension FROM v_extensions WHERE domain_uuid = %s", (d,))
        for row in cur.fetchall():
            _add_if_int(used, row["extension"])
    return used


def _add_if_int(acc: set[int], val) -> None:
    try:
        acc.add(int(val))
    except (TypeError, ValueError):
        pass


def allocate() -> int:
    used = used_extensions()
    for ext in POOL:
        if ext not in used:
            return ext
    raise RuntimeError(f"extension pool {POOL.start}-{POOL.stop - 1} exhausted")


def validate(ext: int) -> int:
    if ext not in POOL:
        raise ValueError(f"extension {ext} outside managed pool {POOL.start}-{POOL.stop - 1}")
    return ext
