"""Create/replace a FusionPBX time-condition dialplan for a planned schedule.

Model: one managed extension (9550-9599) per schedule. The dialplan matches the
extension, then a date-time condition:
  - inside the schedule window  -> answer, play the schedule greeting, then the
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
from dataclasses import dataclass
from datetime import datetime
from xml.sax.saxutils import escape, unescape

from app.config import settings
from app.fpbx.db import cursor, domain_uuid

FS_DT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Closure:
    """One closure window within a time condition."""
    label: str
    start: datetime
    end: datetime
    closed_action: str            # voicemail | hangup
    recording_filename: str       # the greeting recording played when active
    reason: str = ""              # kept only to round-trip the greeting on edit


def _duration(c: Closure):
    return c.end - c.start


def sort_closures(closures: list[Closure]) -> list[Closure]:
    """Most specific first: shortest window wins, ties broken by earliest start.
    A narrow closure therefore shadows a broader one that overlaps it."""
    return sorted(closures, key=lambda c: (_duration(c), c.start))

# Ownership marker embedded in every dialplan we create. We refuse to update or
# delete any dialplan that does not carry it, even if the name/number matches —
# so we never touch a construct a human or another app created.
MARKER = "fpbx-ivr-manager:managed"
_MARKER_XML = f"<!-- {MARKER} -->"

# FusionPBX's fixed app_uuid for the Time Conditions app. Stamping our dialplans
# with it makes them appear as native Time Conditions in the FusionPBX GUI.
# NOTE: we write dialplan_xml directly (not v_dialplan_details), so these must be
# managed through this app. Editing one in the FusionPBX Time Conditions GUI
# regenerates the XML from (absent) details and strips our marker — after which
# our guardrail treats it as foreign and refuses to touch it (fail-safe).
TIME_CONDITIONS_APP_UUID = "4b821450-926b-175a-af93-a03c441818b1"


class NotManaged(Exception):
    """Raised when a targeted construct was not created by this app."""


_RE_TRANSFER = re.compile(r'transfer" data="([^ ]+) XML')
# one closure = its metadata comment immediately followed by its date-time condition
_RE_CLOSURE_META = re.compile(
    r'<!-- ivrmgr:closure label="([^"]*)" reason="([^"]*)" action="([^"]*)" -->'
)
_RE_CONDITION = re.compile(r'<condition date-time="([^"~]+)~([^"]+)"[^>]*>(.*?)</condition>', re.S)
_RE_PLAYBACK = re.compile(r'playback" data="[^"]*?/([^"/]+)"')


def _playback_path(recording_filename: str) -> str:
    return posixpath.join(settings.recordings_dir, recording_filename)


def _comment_safe(text: str) -> str:
    """Make a value safe to embed inside an XML comment attribute."""
    return (text or "").replace('"', "'").replace("<", "").replace(">", "").replace("--", "-")


def _closure_block(extension: int, c: Closure, domain: str) -> str:
    """The metadata comment + date-time condition for one closure."""
    dt = f"{c.start.strftime(FS_DT)}~{c.end.strftime(FS_DT)}"
    play = escape(_playback_path(c.recording_filename))
    if c.closed_action == "hangup":
        closed = '    <action application="hangup" data="NORMAL_CLEARING"/>'
    else:  # voicemail (call already answered above)
        closed = f'    <action application="voicemail" data="default {escape(domain)} {extension}"/>'
    meta = (
        f'  <!-- ivrmgr:closure label="{_comment_safe(c.label)}" '
        f'reason="{_comment_safe(c.reason)}" action="{_comment_safe(c.closed_action)}" -->'
    )
    return (
        f'{meta}\n'
        f'  <condition date-time="{dt}" break="on-true">\n'
        f'    <action application="answer"/>\n'
        f'    <action application="sleep" data="700"/>\n'
        f'    <action application="playback" data="{play}"/>\n'
        f'{closed}\n'
        f'  </condition>'
    )


def build_dialplan_xml(
    extension: int,
    closures: list[Closure],
    *,
    open_destination: str,
    domain: str,
) -> str:
    """A time-condition dialplan holding one or more closures.

    Closures are emitted most-specific-first, each breaking on a match so a live
    closure stops evaluation. If none match, the final condition transfers the
    caller to open_destination (the normal daytime route)."""
    blocks = "\n".join(
        _closure_block(extension, c, domain) for c in sort_closures(closures)
    )
    return (
        f'{_MARKER_XML}\n'
        f'<extension name="schedule_{extension}" continue="false">\n'
        f'  <condition field="destination_number" expression="^{extension}$" break="on-false"/>\n'
        f'{blocks}\n'
        f'  <condition field="destination_number" expression="^{extension}$">\n'
        f'    <action application="transfer" data="{escape(open_destination)} XML {escape(domain)}"/>\n'
        f'  </condition>\n'
        f'</extension>'
    )


def upsert_time_condition(
    extension: int,
    name: str,
    closures: list[Closure],
    *,
    open_destination: str,
) -> str:
    """Create/replace the time-condition dialplan with the given closure set.
    Returns the dialplan name."""
    d = domain_uuid()
    ctx = settings.fpbx_domain_name
    label = name
    name = f"schedule_{extension}"
    xml = build_dialplan_xml(
        extension, closures, open_destination=open_destination, domain=ctx,
    )

    with cursor() as cur:
        # Identity is (our marker + extension number), independent of the dialplan
        # name — so adopted records (any name) update in place too.
        existing = _find_managed_by_number(cur, d, extension)
        if existing:
            cur.execute(
                "UPDATE v_dialplans SET dialplan_number=%s, dialplan_xml=%s, "
                "dialplan_description=%s, dialplan_enabled='true', app_uuid=%s "
                "WHERE dialplan_uuid=%s",
                (str(extension), xml, label, TIME_CONDITIONS_APP_UUID, existing["dialplan_uuid"]),
            )
            name = existing["dialplan_name"]
        else:
            # new record: refuse if anything foreign already occupies the number
            _assert_number_free_or_owned(cur, d, extension)
            dp_uuid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO v_dialplans "
                "(dialplan_uuid, app_uuid, domain_uuid, dialplan_context, dialplan_name, "
                " dialplan_number, dialplan_order, dialplan_enabled, dialplan_xml, "
                " dialplan_description) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'true',%s,%s)",
                (dp_uuid, TIME_CONDITIONS_APP_UUID, d, ctx, name, str(extension), 300, xml, label),
            )
    return name


def _is_managed(xml: str | None) -> bool:
    return bool(xml) and _MARKER_XML in xml


def _find_managed_by_number(cur, d: str, extension: int) -> dict | None:
    """Return our managed dialplan on this number (marker present), or None."""
    cur.execute(
        "SELECT dialplan_uuid, dialplan_name, dialplan_xml FROM v_dialplans "
        "WHERE domain_uuid = %s AND dialplan_number = %s",
        (d, str(extension)),
    )
    for r in cur.fetchall():
        if _is_managed(r["dialplan_xml"]):
            return r
    return None


def _assert_number_free_or_owned(cur, d: str, extension: int) -> None:
    """Refuse if any *foreign* dialplan already lives on this number."""
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


def _parse_dt(a: str, b: str):
    try:
        return datetime.strptime(a, FS_DT), datetime.strptime(b, FS_DT)
    except ValueError:
        return None, None


def parse_closures(xml: str) -> dict:
    """Recover a time condition's closures + open destination from stored XML.

    Handles both the current multi-closure format (metadata comment per closure)
    and the earlier single-closure format (one condition + anti-action)."""
    xml = xml or ""
    metas = _RE_CLOSURE_META.findall(xml)          # [(label, reason, action), ...]
    blocks = _RE_CONDITION.findall(xml)            # [(start, end, inner), ...]
    closures: list[dict] = []
    for i, (a, b, inner) in enumerate(blocks):
        start, end = _parse_dt(a, b)
        meta = metas[i] if i < len(metas) else None
        pm = _RE_PLAYBACK.search(inner)
        closures.append({
            "label": (meta[0] if meta else "") or "closure",
            "reason": meta[1] if meta else "",
            "closed_action": (meta[2] if meta else None)
            or ("voicemail" if "voicemail" in inner else "hangup"),
            "start": start,
            "end": end,
            "recording_filename": pm.group(1) if pm else "",
        })
    # open destination: last transfer wins (our trailing open condition); the old
    # format only had an anti-action transfer, which this regex still catches
    dest = None
    for m in _RE_TRANSFER.finditer(xml):
        dest = unescape(m.group(1))
    return {"closures": closures, "open_destination": dest}


def list_schedules() -> list[dict]:
    """All managed time conditions in the domain (identified by our ownership
    marker), including adopted ones outside the extension pool, by extension.

    Each entry carries its full closure list; for backward compatibility the
    first (most specific) closure's window/action is also surfaced at top level."""
    d = domain_uuid()
    with cursor() as cur:
        # time-condition app_uuid keeps IVR-menu dialplans (which also carry our
        # marker) out of the schedule list
        cur.execute(
            "SELECT dialplan_number, dialplan_name, dialplan_description, "
            "dialplan_enabled, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid = %s AND app_uuid = %s ORDER BY dialplan_number",
            (d, TIME_CONDITIONS_APP_UUID),
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        # the ownership marker is the authoritative scope signal
        if not _is_managed(row["dialplan_xml"]):
            continue
        parsed = parse_closures(row["dialplan_xml"] or "")
        ext = int(row["dialplan_number"])
        first = parsed["closures"][0] if parsed["closures"] else {}
        result.append(
            {
                "extension": ext,
                "label": row["dialplan_description"] or row["dialplan_name"],
                "enabled": row["dialplan_enabled"] == "true",
                # adopted records may sit outside the managed pool; flag for the UI
                "in_pool": settings.ext_pool_start <= ext <= settings.ext_pool_end,
                "open_destination": parsed["open_destination"],
                "closures": parsed["closures"],
                # legacy top-level fields (first closure)
                "start": first.get("start"),
                "end": first.get("end"),
                "closed_action": first.get("closed_action", "voicemail"),
            }
        )
    return result


def list_adoptable() -> list[dict]:
    """Existing FusionPBX Time Conditions in the domain not yet managed by us —
    candidates an admin can manually adopt."""
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_uuid, dialplan_number, dialplan_name, dialplan_description, "
            "dialplan_enabled, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid = %s AND app_uuid = %s ORDER BY dialplan_number",
            (d, TIME_CONDITIONS_APP_UUID),
        )
        rows = cur.fetchall()
    return [
        {
            "dialplan_uuid": r["dialplan_uuid"],
            "extension": int(r["dialplan_number"]) if str(r["dialplan_number"]).isdigit() else r["dialplan_number"],
            "name": r["dialplan_name"],
            "description": r["dialplan_description"] or "",
            "enabled": r["dialplan_enabled"] == "true",
        }
        for r in rows
        if not _is_managed(r["dialplan_xml"])  # exclude ones already ours
    ]


def get_time_condition(dialplan_uuid: str) -> dict:
    """Fetch an adoptable time condition by uuid; raise NotManaged if it isn't a
    FusionPBX time condition or is already managed by us."""
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_uuid, app_uuid, dialplan_number, dialplan_name, "
            "dialplan_description, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid = %s AND dialplan_uuid = %s",
            (d, dialplan_uuid),
        )
        row = cur.fetchone()
    if not row:
        raise NotManaged("time condition not found in this domain")
    if row["app_uuid"] != TIME_CONDITIONS_APP_UUID:
        raise NotManaged("selected dialplan is not a FusionPBX time condition")
    if _is_managed(row["dialplan_xml"]):
        raise NotManaged("this time condition is already managed by the app")
    return {
        "dialplan_uuid": row["dialplan_uuid"],
        "extension": int(row["dialplan_number"]),
        "name": row["dialplan_name"],
        "description": row["dialplan_description"] or "",
    }


def adopt_time_condition(
    dialplan_uuid: str,
    label: str,
    closures: list[Closure],
    *,
    open_destination: str,
) -> int:
    """Convert an existing time condition into an app-managed schedule in place.

    Overwrites the record's dialplan_xml with our schedule model on its own
    extension number. Returns the extension. Admin-gated at the route layer.
    """
    tc = get_time_condition(dialplan_uuid)  # verifies TC + not already managed
    extension = tc["extension"]
    ctx = settings.fpbx_domain_name
    xml = build_dialplan_xml(
        extension, closures, open_destination=open_destination, domain=ctx,
    )
    with cursor() as cur:
        cur.execute(
            "UPDATE v_dialplans SET dialplan_xml=%s, dialplan_description=%s, "
            "dialplan_enabled='true', app_uuid=%s WHERE dialplan_uuid=%s",
            (xml, label, TIME_CONDITIONS_APP_UUID, dialplan_uuid),
        )
    return extension


def get_schedule(extension: int) -> dict | None:
    for c in list_schedules():
        if c["extension"] == extension:
            return c
    return None


def delete_time_condition(extension: int) -> bool:
    d = domain_uuid()
    with cursor() as cur:
        row = _find_managed_by_number(cur, d, extension)
        if not row:
            # nothing of ours on that number — refuse rather than touch a foreign row
            _assert_number_free_or_owned(cur, d, extension)
            return False
        cur.execute(
            "DELETE FROM v_dialplans WHERE dialplan_uuid=%s", (row["dialplan_uuid"],)
        )
        deleted = cur.rowcount > 0
    return deleted
