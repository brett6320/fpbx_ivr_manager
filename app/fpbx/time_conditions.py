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

# FusionPBX's fixed app_uuid for the Time Conditions app. Stamping our dialplans
# with it makes them appear as native Time Conditions in the FusionPBX GUI.
# NOTE: we write dialplan_xml directly (not v_dialplan_details), so these must be
# managed through this app. Editing one in the FusionPBX Time Conditions GUI
# regenerates the XML from (absent) details and strips our marker — after which
# our guardrail treats it as foreign and refuses to touch it (fail-safe).
TIME_CONDITIONS_APP_UUID = "4b821450-926b-175a-af93-a03c441818b1"


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
        f'<extension name="schedule_{extension}" continue="false">\n'
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
    """Create/replace the schedule dialplan. Returns the dialplan name."""
    d = domain_uuid()
    ctx = settings.fpbx_domain_name
    name = f"schedule_{extension}"
    xml = build_dialplan_xml(
        extension,
        ranges,
        recording_filename,
        closed_action=closed_action,
        open_destination=open_destination,
        domain=ctx,
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


def _parse_xml(xml: str) -> dict:
    """Recover schedule fields from a stored dialplan_xml (best-effort)."""
    out: dict = {"start": None, "end": None, "open_destination": None, "closed_action": "hangup"}
    m = _RE_DT.search(xml)
    if m:
        try:
            out["start"] = datetime.strptime(m.group(1), FS_DT)
            out["end"] = datetime.strptime(m.group(2), FS_DT)
        except ValueError:
            pass  # unparseable date-time in stored XML: leave start/end as None
    t = _RE_TRANSFER.search(xml)
    if t:
        out["open_destination"] = unescape(t.group(1))
    if _RE_VOICEMAIL.search(xml):
        out["closed_action"] = "voicemail"
    return out


def list_schedules() -> list[dict]:
    """All managed schedules in the domain (identified by our ownership marker),
    including adopted ones outside the extension pool, by extension number."""
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_number, dialplan_name, dialplan_description, "
            "dialplan_enabled, dialplan_xml FROM v_dialplans "
            "WHERE domain_uuid = %s ORDER BY dialplan_number",
            (d,),
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        # the ownership marker is the authoritative scope signal
        if not _is_managed(row["dialplan_xml"]):
            continue
        parsed = _parse_xml(row["dialplan_xml"] or "")
        ext = int(row["dialplan_number"])
        result.append(
            {
                "extension": ext,
                "label": row["dialplan_description"] or row["dialplan_name"],
                "enabled": row["dialplan_enabled"] == "true",
                # adopted records may sit outside the managed pool; flag for the UI
                "in_pool": settings.ext_pool_start <= ext <= settings.ext_pool_end,
                **parsed,
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
    ranges: tuple[datetime, datetime],
    recording_filename: str,
    *,
    closed_action: str,
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
        extension,
        ranges,
        recording_filename,
        closed_action=closed_action,
        open_destination=open_destination,
        domain=ctx,
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
