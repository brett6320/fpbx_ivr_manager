"""Enumerate the domain's call destinations, mirroring the set FusionPBX offers.

Each destination resolves to a FreeSWITCH transfer target string
`transfer <number> XML <context>` used both for IVR digit options
(`ivr_menu_option_param`) and the IVR timeout/exit (`ivr_menu_exit_data`).

Supported kinds: extension, ring_group, voicemail, plus a free-text manual
number (for an external answering service).
"""
from __future__ import annotations

import re

from app.config import settings
from app.fpbx.db import cursor, domain_uuid

# voicemail deposit uses FusionPBX's default *99<id> "leave a message" prefix
_VM_PREFIX = "*99"


def transfer_action(number: str, context: str | None = None) -> str:
    """Build a FreeSWITCH transfer action target for a destination number."""
    ctx = context or settings.fpbx_domain_name
    return f"transfer {number} XML {ctx}"


def _valid_number(number: str) -> bool:
    # digits, *, #, and + for external — no spaces or dialplan metacharacters
    return bool(re.fullmatch(r"[0-9*#+]{1,20}", number or ""))


def manual_destination(number: str) -> dict:
    if not _valid_number(number):
        raise ValueError(f"invalid destination number: {number!r}")
    return {"kind": "manual", "number": number, "label": f"External / manual: {number}",
            "value": transfer_action(number)}


def list_destinations() -> list[dict]:
    """All selectable destinations in the domain: extensions, ring groups, voicemail."""
    d = domain_uuid()
    out: list[dict] = []
    with cursor() as cur:
        cur.execute(
            "SELECT extension, description FROM v_extensions "
            "WHERE domain_uuid = %s AND (enabled = 'true' OR enabled IS NULL) "
            "ORDER BY extension",
            (d,),
        )
        for r in cur.fetchall():
            num = str(r["extension"])
            label = f"Extension {num}" + (f" — {r['description']}" if r["description"] else "")
            out.append({"kind": "extension", "number": num, "label": label,
                        "value": transfer_action(num)})

        cur.execute(
            "SELECT ring_group_extension, ring_group_name FROM v_ring_groups "
            "WHERE domain_uuid = %s ORDER BY ring_group_extension",
            (d,),
        )
        for r in cur.fetchall():
            num = str(r["ring_group_extension"])
            label = f"Ring group {num} — {r['ring_group_name']}"
            out.append({"kind": "ring_group", "number": num, "label": label,
                        "value": transfer_action(num)})

        cur.execute(
            "SELECT voicemail_id, voicemail_description FROM v_voicemails "
            "WHERE domain_uuid = %s ORDER BY voicemail_id",
            (d,),
        )
        for r in cur.fetchall():
            vid = str(r["voicemail_id"])
            desc = r["voicemail_description"] or ""
            label = f"Voicemail {vid}" + (f" — {desc}" if desc else "")
            out.append({"kind": "voicemail", "number": f"{_VM_PREFIX}{vid}", "label": label,
                        "value": transfer_action(f"{_VM_PREFIX}{vid}")})
    return out
