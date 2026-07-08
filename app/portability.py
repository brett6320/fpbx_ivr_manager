"""Export / import of app-managed items (business profile, schedules, IVRs).

Import is never automatic: the document is turned into a list of review items,
each editable, and only the ones an admin explicitly includes are committed.
"""
from __future__ import annotations

from app import business, service
from app.fpbx import ivr_menus, time_conditions
from app.models import IvrOption, IvrRequest, ScheduleRequest

EXPORT_VERSION = 1


def export_document() -> dict:
    """Serialize the managed items into a portable JSON-able document."""
    schedules = []
    for s in time_conditions.list_schedules():
        schedules.append({
            "extension": s["extension"],
            "label": s["label"],
            "start": s["start"].isoformat() if s.get("start") else None,
            "end": s["end"].isoformat() if s.get("end") else None,
            "closed_action": s.get("closed_action", "voicemail"),
            "open_destination": s.get("open_destination"),
            "reason": "",  # not recoverable from the dialplan; edit on import
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
        req = ScheduleRequest(
            label=data["label"],
            start=data["start"],
            end=data["end"],
            reason=data.get("reason") or None,
            extension=data.get("extension"),
            closed_action=data.get("closed_action", "voicemail"),
        )
        open_dest = (data.get("open_destination") or "").strip()
        if not open_dest:
            raise ValueError("schedule needs an open_destination")
        r = service.apply_schedule(req, open_dest)
        return f"Schedule created on extension {r.extension}"

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
