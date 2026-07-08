"""Export / import of app-managed items (business profile, schedules, IVRs).

Import is never automatic: the document is turned into a list of review items,
each editable, and only the ones an admin explicitly includes are committed.
"""
from __future__ import annotations

from app import business, service
from app.fpbx import ivr_menus, time_conditions
from app.models import Closure, IvrOption, IvrRequest, TimeConditionRequest

EXPORT_VERSION = 2


def _dump_closure(cl: dict) -> dict:
    return {
        "label": cl.get("label", ""),
        "start": cl["start"].isoformat() if cl.get("start") else None,
        "end": cl["end"].isoformat() if cl.get("end") else None,
        "reason": cl.get("reason", ""),
        "closed_action": cl.get("closed_action", "voicemail"),
    }


def export_document() -> dict:
    """Serialize the managed items into a portable JSON-able document."""
    schedules = []
    for s in time_conditions.list_schedules():
        schedules.append({
            "extension": s["extension"],
            "label": s["label"],
            "open_destination": s.get("open_destination"),
            "closures": [_dump_closure(cl) for cl in s.get("closures", [])],
        })
    ivrs = [ivr_menus.get_ivr_full(i["extension"]) for i in ivr_menus.list_ivrs()]
    return {
        "version": EXPORT_VERSION,
        "business": {
            "business_name": business.load().get("business_name", ""),
            "templates": business.templates(),
        },
        "schedules": schedules,
        "ivrs": [i for i in ivrs if i],
    }


def to_review_items(doc: dict) -> list[dict]:
    """Flatten a document into individually-reviewable items."""
    items: list[dict] = []
    biz = doc.get("business")
    if isinstance(biz, dict) and (biz.get("business_name") or biz.get("templates")):
        items.append({"type": "business", "label": "Business profile", "data": biz})
    for s in doc.get("schedules") or []:
        items.append({"type": "schedule",
                      "label": f"Schedule {s.get('extension')} — {s.get('label', '')}", "data": s})
    for v in doc.get("ivrs") or []:
        items.append({"type": "ivr",
                      "label": f"IVR {v.get('extension')} — {v.get('name', '')}", "data": v})
    return items


def commit_item(item_type: str, data: dict) -> str:
    """Apply a single reviewed item. Returns a human-readable result string.
    Raises ValueError on bad input."""
    if item_type == "business":
        business.save(data.get("business_name", ""), data.get("templates") or {})
        return "Business profile saved"

    if item_type == "schedule":
        open_dest = (data.get("open_destination") or "").strip()
        if not open_dest:
            raise ValueError("schedule needs an open_destination")
        # v2 carries a closure list; v1 was a single flat closure
        raw = data.get("closures")
        if not raw:
            raw = [{
                "label": data.get("label", "closure"),
                "start": data.get("start"), "end": data.get("end"),
                "reason": data.get("reason"),
                "closed_action": data.get("closed_action", "voicemail"),
            }]
        closures = [
            Closure(
                label=c.get("label", "closure"), start=c["start"], end=c["end"],
                reason=c.get("reason") or None,
                closed_action=c.get("closed_action", "voicemail"),
            )
            for c in raw if c.get("start") and c.get("end")
        ]
        tc = TimeConditionRequest(
            name=data.get("label", "TC"), open_destination=open_dest,
            extension=data.get("extension"), closures=closures,
        )
        r = service.apply_time_condition(tc)
        return f"Time condition created on extension {r.extension} ({r.closure_count} closure(s))"

    if item_type == "ivr":
        options = [IvrOption(**o) for o in (data.get("options") or [])]
        req = IvrRequest(
            name=data["name"],
            extension=data.get("extension"),
            greeting_text=data.get("greeting_text") or None,
            greeting_recording=data.get("greeting_recording") or None,
            timeout_destination=data["timeout_destination"],
            options=options,
        )
        r = service.create_ivr(req)
        return f"IVR created on extension {r.extension}"

    raise ValueError(f"unknown item type: {item_type!r}")
