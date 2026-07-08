"""Manage FusionPBX inbound routes (Destinations) — dialplans in the 'public'
context that match an incoming DID and transfer it into the domain.

Used by the call-flow wizard to complete: DID (inbound route) -> time condition
(schedule) -> IVR. Managed with our ownership marker; we never touch a foreign
inbound route for a DID we didn't create.
"""
from __future__ import annotations

import uuid
from xml.sax.saxutils import escape

from app.config import settings
from app.fpbx.db import cursor, domain_uuid
from app.fpbx.time_conditions import MARKER, NotManaged

# FusionPBX fixed app_uuid for inbound Destinations (dialplan shows natively).
INBOUND_APP_UUID = "c03b422e-13a8-bd1b-e42b-b6b9b4d27ce4"
PUBLIC_CONTEXT = "public"
_MARKER_XML = f"<!-- {MARKER} -->"


def build_inbound_xml(did: str, dest_number: str, dom_uuid: str, dom_name: str) -> str:
    """Inbound route: match the DID, tag the call, transfer into the domain."""
    return "\n".join([
        _MARKER_XML,
        f'<extension name="{escape(did)}" continue="false">',
        f'  <condition field="destination_number" expression="^{escape(did)}$">',
        '    <action application="export" data="call_direction=inbound" inline="true"/>',
        f'    <action application="set" data="domain_uuid={dom_uuid}" inline="true"/>',
        f'    <action application="set" data="domain_name={escape(dom_name)}" inline="true"/>',
        f'    <action application="transfer" data="{escape(dest_number)} XML {escape(dom_name)}"/>',
        '  </condition>',
        '</extension>',
    ])


def _is_managed(xml: str | None) -> bool:
    return bool(xml) and _MARKER_XML in xml


def list_inbound_destinations() -> list[dict]:
    """Existing inbound routes (public-context DIDs) in the domain, for selection.

    Each carries `managed` (created by this app) so the UI can warn before an
    overwrite would replace a route the app didn't create.
    """
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_number, dialplan_name, dialplan_enabled, dialplan_xml "
            "FROM v_dialplans WHERE domain_uuid = %s AND dialplan_context = %s "
            "AND dialplan_number IS NOT NULL AND dialplan_number <> '' "
            "ORDER BY dialplan_number",
            (d, PUBLIC_CONTEXT),
        )
        rows = cur.fetchall()
    return [
        {
            "did": r["dialplan_number"],
            "name": r["dialplan_name"],
            "enabled": r["dialplan_enabled"] == "true",
            "managed": _is_managed(r["dialplan_xml"]),
        }
        for r in rows
    ]


def is_foreign_route(did: str) -> bool:
    """True if an inbound route for this DID exists and was NOT created by us."""
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_xml FROM v_dialplans "
            "WHERE dialplan_context = %s AND dialplan_number = %s",
            (PUBLIC_CONTEXT, did),
        )
        row = cur.fetchone()
    return bool(row) and not _is_managed(row["dialplan_xml"])


def upsert_inbound(
    did: str, dest_number: str, name: str | None = None, *, confirm_overwrite: bool = False
) -> str:
    """Create/replace a managed inbound route for `did` that transfers to
    `dest_number` inside the domain. Returns the dialplan name.

    Safeguard: if a route for `did` already exists and was NOT created by this
    app, it is only replaced when `confirm_overwrite=True` — otherwise this
    raises NotManaged rather than stomping on the existing route.
    """
    d = domain_uuid()
    dom_name = settings.fpbx_domain_name
    name = name or f"inbound_{did}"
    xml = build_inbound_xml(did, dest_number, d, dom_name)

    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_uuid, dialplan_xml FROM v_dialplans "
            "WHERE dialplan_context = %s AND dialplan_number = %s",
            (PUBLIC_CONTEXT, did),
        )
        row = cur.fetchone()
        if row and not _is_managed(row["dialplan_xml"]) and not confirm_overwrite:
            raise NotManaged(
                f"an inbound route for {did!r} already exists and was not created by "
                f"this app — confirm the overwrite to replace it"
            )
        if row:
            cur.execute(
                "UPDATE v_dialplans SET dialplan_xml=%s, dialplan_name=%s, "
                "dialplan_enabled='true', app_uuid=%s WHERE dialplan_uuid=%s",
                (xml, name, INBOUND_APP_UUID, row["dialplan_uuid"]),
            )
        else:
            cur.execute(
                "INSERT INTO v_dialplans "
                "(dialplan_uuid, app_uuid, domain_uuid, dialplan_context, dialplan_name, "
                " dialplan_number, dialplan_order, dialplan_enabled, dialplan_xml) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'true',%s)",
                (str(uuid.uuid4()), INBOUND_APP_UUID, d, PUBLIC_CONTEXT, name, did, 100, xml),
            )
    return name


def delete_inbound(did: str) -> bool:
    with cursor() as cur:
        cur.execute(
            "SELECT dialplan_uuid, dialplan_xml FROM v_dialplans "
            "WHERE dialplan_context = %s AND dialplan_number = %s",
            (PUBLIC_CONTEXT, did),
        )
        row = cur.fetchone()
        if not row:
            return False
        if not _is_managed(row["dialplan_xml"]):
            raise NotManaged(f"inbound route for {did!r} was not created by this app")
        cur.execute("DELETE FROM v_dialplans WHERE dialplan_uuid=%s", (row["dialplan_uuid"],))
    return True
