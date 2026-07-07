"""Create/replace a FusionPBX time-condition dialplan for a planned closure.

Model: one managed extension (9550-9599) per closure. The dialplan matches the
extension, then a date-time condition:
  - inside the closure window  -> answer, play the closure greeting, then the
    closed action (voicemail or hangup)
  - outside the window         -> transfer the caller to the normal daytime
    destination (an existing IVR / ring group / extension)

FreeSWITCH consumes v_dialplans.dialplan_xml, so that column is the source of
truth; detail rows are written so the GUI stays coherent.
"""
from __future__ import annotations

import posixpath
import re
import uuid
from datetime import datetime
from xml.sax.saxutils import escape, unescape

from app.config import settings
from app.fpbx.db import cursor, domain_uuid

FS_DT = "%Y-%m-%d %H:%M:%S"

# Ownership marker embedded in every dialplan we create. We refuse to update or
# delete any dialplan that does not carry it, even if the name/number matches —
# so we never touch a construct a human or another app created.
MARKER = "fpbx-ivr-manager:managed"
_MARKER_XML = f"<!-- {MARKER} -->"


class NotManaged(Exception):
    """Raised when a targeted construct was not created by this app."""


_RE_DT = re.compile(r'date-time="([^"~]+)~([^"]+)"')
_RE_TRANSFER = re.compile(r'transfer" data="([^ ]+) XML')
_RE_VOICEMAIL = re.compile(r'application="voicemail"')


def _playback_path(recording_filename: str) -> str:
    return posixpath.join(settings.recordings_dir, recording_filename)


def build_dialplan_xml(
    extension: int,
    ranges: tuple[datetime, datetime],
    recording_filename: str,
    *,
    closed_action: str,
    open_destination: str,
    domain: str,
) -> str:
    start, end = ranges
    dt = f"{start.strftime(FS_DT)}~{end.strftime(FS_DT)}"
    play = escape(_playback_path(recording_filename))

    if closed_action == "hangup":
        closed = '    <action application="hangup" data="NORMAL_CLEARING"/>'
    else:  # voicemail
        closed = (
            f'    <action application="answer"/>\n'
            f'    <action application="sleep" data="700"/>\n'
            f'    <action application="voicemail" data="default {escape(domain)} {extension}"/>'
        )

    return (
        f'{_MARKER_XML}\n'
        f'<extension name="closure_{extension}" continue="false">\n'
        f'  <condition field="destination_number" expression="^{extension}$" break="on-false"/>\n'
        f'  <condition date-time="{dt}">\n'
        f'    <action application="answer"/>\n'
        f'    <action application="sleep" data="700"/>\n'
        f'    <action application="playback" data="{play}"/>\n'
        f"{closed}\n"
        f'    <anti-action application="transfer" data="{escape(open_destination)} XML {escape(domain)}"/>\n'
        f'  </condition>\n'
        f'</extension>'
    )


def upsert_time_condition(
    extension: int,
    label: str,
    ranges: tuple[datetime, datetime],
    recording_filename: str,
    *,
    closed_action: str,
    open_destination: str,
) -> str:
    """Create/replace the closure dialplan. Returns the dialplan name."""
    d = domain_uuid()
    ctx = settings.fpbx_domain_name
    name = f"closure_{extension}"
    xml = build_dialplan_xml(
        extension,
        ranges,
        recording_filename,
        closed_action=closed_action,
        open_destination=open_destination,
        domain=ctx,
    )

    with cursor() as cur:
        # Guardrail 1: nothing foreign may occupy this pool number.
        _assert_number_free_or_owned(cur, d, extension)

        # Guardrail 2: if a row already carries our name, it must be one of ours.
        cur.execute(
            "SELECT dialplan_uuid, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid = %s AND dialplan_name = %s",
            (d, name),
        )
        row = cur.fetchone()
        if row:
            if not _is_managed(row["dialplan_xml"]):
                raise NotManaged(
                    f"dialplan {name!r} exists but was not created by this app; refusing to overwrite"
                )
            dp_uuid = row["dialplan_uuid"]
            cur.execute(
                "UPDATE v_dialplans SET dialplan_number=%s, dialplan_xml=%s, "
                "dialplan_description=%s, dialplan_enabled='true' WHERE dialplan_uuid=%s",
                (str(extension), xml, label, dp_uuid),
            )
        else:
            dp_uuid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO v_dialplans "
                "(dialplan_uuid, domain_uuid, dialplan_context, dialplan_name, "
                " dialplan_number, dialplan_order, dialplan_enabled, dialplan_xml, "
                " dialplan_description) "
                "VALUES (%s,%s,%s,%s,%s,%s,'true',%s,%s)",
                (dp_uuid, d, ctx, name, str(extension), 300, xml, label),
            )
    return name


def _is_managed(xml: str | None) -> bool:
    return bool(xml) and _MARKER_XML in xml


def _assert_number_free_or_owned(cur, d: str, extension: int) -> None:
    """Refuse if any *foreign* dialplan already lives on this pool number."""
    cur.execute(
        "SELECT dialplan_name, dialplan_xml FROM v_dialplans "
        "WHERE domain_uuid = %s AND dialplan_number = %s",
        (d, str(extension)),
    )
    for r in cur.fetchall():
        if not _is_managed(r["dialplan_xml"]):
            raise NotManaged(
                f"extension {extension} is already used by dialplan "
                f"{r['dialplan_name']!r}, which this app did not create; refusing to touch it"
            )


def _parse_xml(xml: str) -> dict:
    """Recover closure fields from a stored dialplan_xml (best-effort)."""
    out: dict = {"start": None, "end": None, "open_destination": None, "closed_action": "hangup"}
    m = _RE_DT.search(xml)
    if m:
        try:
            out["start"] = datetime.strptime(m.group(1), FS_DT)
            out["end"] = datetime.strptime(m.group(2), FS_DT)
        except ValueError:
            pass
    t = _RE_TRANSFER.search(xml)
    if t:
        out["open_destination"] = unescape(t.group(1))
    if _RE_VOICEMAIL.search(xml):
        out["closed_action"] = "voicemail"
    return out


def list_closures() -> list[dict]:
    """All managed closure dialplans in the domain, newest extension first."""
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_number, dialplan_name, dialplan_description, "
            "dialplan_enabled, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid = %s AND dialplan_name LIKE 'closure_%%' "
            "ORDER BY dialplan_number",
            (d,),
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        # defense in depth: only surface rows carrying our ownership marker,
        # so we never offer to edit/delete a look-alike we didn't create.
        if not _is_managed(row["dialplan_xml"]):
            continue
        parsed = _parse_xml(row["dialplan_xml"] or "")
        result.append(
            {
                "extension": int(row["dialplan_number"]),
                "label": row["dialplan_description"] or row["dialplan_name"],
                "enabled": row["dialplan_enabled"] == "true",
                **parsed,
            }
        )
    return result


def get_closure(extension: int) -> dict | None:
    for c in list_closures():
        if c["extension"] == extension:
            return c
    return None


def delete_time_condition(extension: int) -> bool:
    d = domain_uuid()
    name = f"closure_{extension}"
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_uuid, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid=%s AND dialplan_name=%s",
            (d, name),
        )
        row = cur.fetchone()
        if not row:
            return False
        if not _is_managed(row["dialplan_xml"]):
            raise NotManaged(
                f"dialplan {name!r} was not created by this app; refusing to delete"
            )
        cur.execute(
            "DELETE FROM v_dialplans WHERE dialplan_uuid=%s", (row["dialplan_uuid"],)
        )
        deleted = cur.rowcount > 0
    return deleted
