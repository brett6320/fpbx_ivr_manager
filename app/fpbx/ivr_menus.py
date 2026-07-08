"""Manage FusionPBX IVR menus (v_ivr_menus + v_ivr_menu_options + dialplan).

FusionPBX drives IVRs with a Lua script (ivr_menu.lua) that reads the menu and
its options live from the database; the dialplan just answers, sets
ivr_menu_uuid, calls the lua, then runs the exit/timeout action. So we insert the
DB rows plus a small dialplan and reloadxml — no FreeSWITCH ivr-config generation.

Managed the same way as schedules: our ownership marker (in ivr_menu_description
and the dialplan XML) is authoritative; we never touch an IVR we didn't create.
"""
from __future__ import annotations

import uuid
from xml.sax.saxutils import escape

from app.config import settings
from app.fpbx.db import cursor, domain_uuid, insert_row
from app.fpbx.time_conditions import MARKER, NotManaged

# FusionPBX fixed app_uuid for the IVR Menus app (dialplan appears natively).
IVR_MENUS_APP_UUID = "a5788e9b-58bc-bd1b-df59-fff5d51253ab"
_MARKER_XML = f"<!-- {MARKER} -->"
_DESC_TAG = f"[{MARKER}]"

# FusionPBX defaults
DEFAULT_TIMEOUT_MS = 3000
DEFAULT_DIGIT_LEN = 5
DEFAULT_INTER_DIGIT_TIMEOUT = 2000
DEFAULT_MAX_FAILURES = 3
DEFAULT_MAX_TIMEOUTS = 3


def _split_action(action: str) -> tuple[str, str]:
    """'transfer 2000 XML domain' -> ('transfer', '2000 XML domain')."""
    parts = action.split(" ", 1)
    return (parts[0], parts[1] if len(parts) > 1 else "")


def build_ivr_dialplan_xml(extension: int, name: str, ivr_menu_uuid: str,
                           timeout_action: str) -> str:
    """The dialplan that answers and hands the call to ivr_menu.lua, then runs
    the timeout/exit action when the menu returns."""
    exit_app, exit_data = _split_action(timeout_action)
    lines = [
        _MARKER_XML,
        f'<extension name="{escape(name)}" continue="false">',
        f'  <condition field="destination_number" expression="^{extension}$">',
        '    <action application="ring_ready" data=""/>',
        '    <action application="answer" data=""/>',
        '    <action application="sleep" data="1000"/>',
        '    <action application="set" data="hangup_after_bridge=true"/>',
        f'    <action application="set" data="ivr_menu_uuid={escape(ivr_menu_uuid)}"/>',
        '    <action application="lua" data="ivr_menu.lua"/>',
    ]
    if exit_app:
        lines.append(f'    <action application="{escape(exit_app)}" data="{escape(exit_data)}"/>')
    lines += ['  </condition>', '</extension>']
    return "\n".join(lines)


def _is_managed_desc(description: str | None) -> bool:
    return bool(description) and _DESC_TAG in description


def upsert_ivr(
    extension: int,
    name: str,
    greet_long: str,
    options: list[tuple[str, str]],
    *,
    timeout_action: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> str:
    """Create/replace a managed IVR menu on `extension`.

    options: list of (digits, action) where action is 'transfer <n> XML <ctx>'.
    timeout_action: same form; becomes ivr_menu_exit_app/exit_data.
    Returns the ivr_menu_uuid.
    """
    d = domain_uuid()
    ctx = settings.fpbx_domain_name
    description = f"{name} {_DESC_TAG}".strip()
    exit_app, exit_data = _split_action(timeout_action)

    with cursor() as cur:
        # find our existing managed IVR on this number, if any
        cur.execute(
            "SELECT ivr_menu_uuid, dialplan_uuid, ivr_menu_description FROM v_ivr_menus "
            "WHERE domain_uuid = %s AND ivr_menu_extension = %s",
            (d, str(extension)),
        )
        row = cur.fetchone()
        if row and not _is_managed_desc(row["ivr_menu_description"]):
            raise NotManaged(
                f"IVR on extension {extension} exists but was not created by this app"
            )

        if row:
            ivr_uuid = row["ivr_menu_uuid"]
            dp_uuid = row["dialplan_uuid"]
        else:
            ivr_uuid = str(uuid.uuid4())
            dp_uuid = str(uuid.uuid4())

        xml = build_ivr_dialplan_xml(extension, name, ivr_uuid, timeout_action)

        # dialplan (native IVR app_uuid so it appears under IVR menus)
        _upsert_dialplan(cur, dp_uuid, d, ctx, name, extension, xml)

        # ivr menu row
        if row:
            cur.execute(
                "UPDATE v_ivr_menus SET ivr_menu_name=%s, ivr_menu_greet_long=%s, "
                "ivr_menu_timeout=%s, ivr_menu_exit_app=%s, ivr_menu_exit_data=%s, "
                "ivr_menu_description=%s, ivr_menu_enabled='true' WHERE ivr_menu_uuid=%s",
                (name, greet_long, timeout_ms, exit_app, exit_data, description, ivr_uuid),
            )
            cur.execute("DELETE FROM v_ivr_menu_options WHERE ivr_menu_uuid=%s", (ivr_uuid,))
        else:
            # insert only columns this FusionPBX version actually has (e.g. some
            # schemas lack ivr_menu_context) — see db.insert_row
            insert_row(cur, "v_ivr_menus", {
                "ivr_menu_uuid": ivr_uuid,
                "domain_uuid": d,
                "dialplan_uuid": dp_uuid,
                "ivr_menu_name": name,
                "ivr_menu_extension": str(extension),
                "ivr_menu_greet_long": greet_long,
                "ivr_menu_timeout": timeout_ms,
                "ivr_menu_inter_digit_timeout": DEFAULT_INTER_DIGIT_TIMEOUT,
                "ivr_menu_max_failures": DEFAULT_MAX_FAILURES,
                "ivr_menu_max_timeouts": DEFAULT_MAX_TIMEOUTS,
                "ivr_menu_digit_len": DEFAULT_DIGIT_LEN,
                "ivr_menu_exit_app": exit_app,
                "ivr_menu_exit_data": exit_data,
                "ivr_menu_direct_dial": "false",
                "ivr_menu_context": ctx,
                "ivr_menu_enabled": "true",
                "ivr_menu_description": description,
            })

        # options
        for order, (digits, action) in enumerate(options, start=1):
            insert_row(cur, "v_ivr_menu_options", {
                "ivr_menu_option_uuid": str(uuid.uuid4()),
                "ivr_menu_uuid": ivr_uuid,
                "domain_uuid": d,
                "ivr_menu_option_digits": digits,
                "ivr_menu_option_action": "menu-exec-app",
                "ivr_menu_option_param": action,
                "ivr_menu_option_order": order,
                "ivr_menu_option_enabled": "true",
            })
    return ivr_uuid


def _upsert_dialplan(cur, dp_uuid, d, ctx, name, extension, xml) -> None:
    cur.execute(
        "SELECT dialplan_uuid FROM v_dialplans WHERE dialplan_uuid=%s", (dp_uuid,)
    )
    if cur.fetchone():
        cur.execute(
            "UPDATE v_dialplans SET dialplan_xml=%s, dialplan_number=%s, dialplan_name=%s, "
            "dialplan_enabled='true', app_uuid=%s WHERE dialplan_uuid=%s",
            (xml, str(extension), name, IVR_MENUS_APP_UUID, dp_uuid),
        )
    else:
        cur.execute(
            "INSERT INTO v_dialplans "
            "(dialplan_uuid, app_uuid, domain_uuid, dialplan_context, dialplan_name, "
            " dialplan_number, dialplan_order, dialplan_enabled, dialplan_xml) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'true',%s)",
            (dp_uuid, IVR_MENUS_APP_UUID, d, ctx, name, str(extension), 300, xml),
        )


def list_ivrs() -> list[dict]:
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT ivr_menu_uuid, ivr_menu_extension, ivr_menu_name, ivr_menu_description, "
            "ivr_menu_enabled FROM v_ivr_menus WHERE domain_uuid = %s ORDER BY ivr_menu_extension",
            (d,),
        )
        rows = cur.fetchall()
    result = []
    for r in rows:
        if not _is_managed_desc(r["ivr_menu_description"]):
            continue
        result.append({
            "ivr_menu_uuid": r["ivr_menu_uuid"],
            "extension": int(r["ivr_menu_extension"]),
            "name": r["ivr_menu_name"],
            "enabled": r["ivr_menu_enabled"] == "true",
        })
    return result


def get_ivr(extension: int) -> dict | None:
    for i in list_ivrs():
        if i["extension"] == extension:
            return i
    return None


def delete_ivr(extension: int) -> bool:
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT ivr_menu_uuid, dialplan_uuid, ivr_menu_description FROM v_ivr_menus "
            "WHERE domain_uuid=%s AND ivr_menu_extension=%s",
            (d, str(extension)),
        )
        row = cur.fetchone()
        if not row:
            return False
        if not _is_managed_desc(row["ivr_menu_description"]):
            raise NotManaged(f"IVR on extension {extension} was not created by this app")
        cur.execute("DELETE FROM v_ivr_menu_options WHERE ivr_menu_uuid=%s", (row["ivr_menu_uuid"],))
        cur.execute("DELETE FROM v_ivr_menus WHERE ivr_menu_uuid=%s", (row["ivr_menu_uuid"],))
        if row["dialplan_uuid"]:
            cur.execute("DELETE FROM v_dialplans WHERE dialplan_uuid=%s", (row["dialplan_uuid"],))
    return True
