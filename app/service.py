"""Orchestrate a schedule: phrase -> TTS -> recording -> time condition -> reload."""
from __future__ import annotations

import re

from app import business
from app.fpbx import (
    extensions,
    inbound_routes,
    ivr_menus,
    recordings,
    time_conditions,
    xmlrpc_client,
)
from app.models import (
    CallFlowResult,
    IvrRequest,
    IvrResult,
    ScheduleRequest,
    ScheduleResult,
)
from app.phrases.builder import build_phrase
from app.tts import google_tts


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "schedule"


def preview_phrase(req: ScheduleRequest) -> str:
    return build_phrase(
        req.start, req.end,
        org_name=business.business_name(),
        reason=business.render(req.reason),
    ).text


def _synthesize_recording(ext: int, req: ScheduleRequest):
    """Build the greeting and store it as a recording. Returns (phrase, rec_name, filename)."""
    phrase = build_phrase(
        req.start, req.end,
        org_name=business.business_name(),
        reason=business.render(req.reason),
    )
    wav = google_tts.synthesize(phrase.text)
    rec_name = f"schedule_{ext}_{_slug(req.label)}"
    rec_filename = recordings.upsert_recording(rec_name, wav, description=req.label)
    return phrase, rec_name, rec_filename


def apply_schedule(req: ScheduleRequest, open_destination: str) -> ScheduleResult:
    if req.extension:
        # editing an existing managed schedule keeps its number (may be outside the
        # pool if it was adopted); a brand-new one must be in the managed pool.
        ext = req.extension if time_conditions.get_schedule(req.extension) else extensions.validate(req.extension)
    else:
        ext = extensions.allocate()

    phrase, rec_name, rec_filename = _synthesize_recording(ext, req)

    tc_name = time_conditions.upsert_time_condition(
        ext,
        req.label,
        (req.start, req.end),
        rec_filename,
        closed_action=req.closed_action,
        open_destination=open_destination,
    )
    reloaded = xmlrpc_client.reloadxml()
    return ScheduleResult(
        extension=ext,
        label=req.label,
        phrase_text=phrase.text,
        recording_name=rec_name,
        time_condition_name=tc_name,
        reloaded=reloaded,
    )


def adopt_schedule(dialplan_uuid: str, req: ScheduleRequest, open_destination: str) -> ScheduleResult:
    """Admin-only: convert an existing FusionPBX time condition into a managed schedule."""
    tc = time_conditions.get_time_condition(dialplan_uuid)  # verifies + gives extension
    ext = tc["extension"]

    phrase, rec_name, rec_filename = _synthesize_recording(ext, req)

    time_conditions.adopt_time_condition(
        dialplan_uuid,
        req.label,
        (req.start, req.end),
        rec_filename,
        closed_action=req.closed_action,
        open_destination=open_destination,
    )
    reloaded = xmlrpc_client.reloadxml()
    return ScheduleResult(
        extension=ext,
        label=req.label,
        phrase_text=phrase.text,
        recording_name=rec_name,
        time_condition_name=tc["name"],
        reloaded=reloaded,
    )


def list_adoptable() -> list[dict]:
    return time_conditions.list_adoptable()


def get_adoptable(dialplan_uuid: str) -> dict:
    return time_conditions.get_time_condition(dialplan_uuid)


def list_schedules() -> list[dict]:
    return time_conditions.list_schedules()


def get_schedule(extension: int) -> dict | None:
    return time_conditions.get_schedule(extension)


def delete_schedule(extension: int) -> bool:
    deleted = time_conditions.delete_time_condition(extension)
    if deleted:
        xmlrpc_client.reloadxml()
    return deleted


# ---- IVR menus ----
def create_ivr(req: IvrRequest) -> IvrResult:
    if req.extension:
        ext = req.extension if ivr_menus.get_ivr(req.extension) else extensions.validate(req.extension)
    else:
        ext = extensions.allocate()

    if req.greeting_text:
        wav = google_tts.synthesize(business.render(req.greeting_text))
        greet_filename = recordings.upsert_recording(
            f"ivr_{ext}_{_slug(req.name)}", wav, description=req.name
        )
    elif req.greeting_recording:
        greet_filename = req.greeting_recording
    else:
        raise ValueError("an IVR greeting (TTS text or an existing recording) is required")

    options = [(o.digits, o.destination) for o in req.options]
    ivr_uuid = ivr_menus.upsert_ivr(
        ext, req.name, greet_filename, options, timeout_action=req.timeout_destination
    )
    reloaded = xmlrpc_client.reloadxml()
    return IvrResult(
        extension=ext, name=req.name, ivr_menu_uuid=ivr_uuid,
        option_count=len(options), reloaded=reloaded,
    )


def list_ivrs() -> list[dict]:
    return ivr_menus.list_ivrs()


def delete_ivr(extension: int) -> bool:
    deleted = ivr_menus.delete_ivr(extension)
    if deleted:
        xmlrpc_client.reloadxml()
    return deleted


def list_destinations() -> list[dict]:
    from app.fpbx import destinations
    return destinations.list_destinations()


def list_recordings() -> list[dict]:
    return recordings.list_recordings()


# ---- one-flow call flow: inbound route -> time condition -> IVR ----
def build_call_flow(
    inbound_did: str | None, schedule_req: ScheduleRequest, ivr_req: IvrRequest
) -> CallFlowResult:
    """Create the IVR, a schedule whose open destination routes into it, and
    (optionally) an inbound route for the DID that points at the schedule."""
    ivr = create_ivr(ivr_req)                                  # -> IVR extension
    sched = apply_schedule(schedule_req, str(ivr.extension))   # open dest = IVR
    inbound_name = None
    reloaded = sched.reloaded
    if inbound_did:
        inbound_name = inbound_routes.upsert_inbound(inbound_did, str(sched.extension))
        reloaded = xmlrpc_client.reloadxml()
    return CallFlowResult(
        inbound_number=inbound_did or None,
        inbound_name=inbound_name,
        schedule_extension=sched.extension,
        ivr_extension=ivr.extension,
        ivr_name=ivr.name,
        option_count=ivr.option_count,
        reloaded=reloaded,
    )
