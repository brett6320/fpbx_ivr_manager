"""Orchestrate a schedule: phrase -> TTS -> recording -> time condition -> reload."""
from __future__ import annotations

import re

from app import business, recycle
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
    TimeConditionRequest,
)
from app.phrases.builder import build_phrase
from app.tts import google_tts

# Prefix on recordings this app generates via TTS, so they're easy to identify
# and filter (in the "existing phrase" picker and in FusionPBX recordings).
GENERATED_PREFIX = "ivrmgr_"


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "schedule"


def _closure_parts() -> tuple[str | None, str | None]:
    """Business-configured opening/closing for the closure greeting, with
    placeholders resolved. None means 'use the builder default'."""
    opening = business.render(business.closure_opening()) or None
    closing = business.render(business.closure_closing()) or None
    return opening, closing


def preview_phrase(req: ScheduleRequest) -> str:
    opening, closing = _closure_parts()
    return build_phrase(
        req.start, req.end,
        org_name=business.business_name(),
        reason=business.render(req.reason),
        opening=opening, closing=closing,
    ).text


def _synthesize_recording(ext: int, req: ScheduleRequest):
    """Build the greeting and store it as a recording. Returns (phrase, rec_name, filename)."""
    opening, closing = _closure_parts()
    phrase = build_phrase(
        req.start, req.end,
        org_name=business.business_name(),
        reason=business.render(req.reason),
        opening=opening, closing=closing,
    )
    wav = google_tts.synthesize(phrase.text)
    rec_name = f"{GENERATED_PREFIX}schedule_{ext}_{_slug(req.label)}"
    rec_filename = recordings.upsert_recording(rec_name, wav, description=req.label)
    return phrase, rec_name, rec_filename


def _resolve_extension(ext_in: int | None) -> int:
    if ext_in:
        # editing an existing managed TC keeps its number (may be outside the pool
        # if adopted); a brand-new one must be in the managed pool.
        return ext_in if time_conditions.get_schedule(ext_in) else extensions.validate(ext_in)
    return extensions.allocate()


def _build_closure(ext: int, c) -> tuple[str, time_conditions.Closure]:
    """Synthesize the greeting for one closure and return (phrase_text, Closure)."""
    phrase, _rec_name, rec_filename = _synthesize_recording(ext, c)
    return phrase.text, time_conditions.Closure(
        label=c.label, start=c.start, end=c.end,
        closed_action=c.closed_action, recording_filename=rec_filename,
        reason=c.reason or "",
    )


def apply_time_condition(tc: TimeConditionRequest) -> ScheduleResult:
    """Create/replace a time condition holding one or more closures."""
    ext = _resolve_extension(tc.extension)
    phrases, closures = [], []
    for c in tc.closures:
        text, closure = _build_closure(ext, c)
        phrases.append(text)
        closures.append(closure)

    name = time_conditions.upsert_time_condition(
        ext, tc.name, closures, open_destination=tc.open_destination,
    )
    reloaded = xmlrpc_client.reloadxml()
    first = time_conditions.sort_closures(closures)[0]
    return ScheduleResult(
        extension=ext,
        label=tc.name,
        phrase_text=phrases[closures.index(first)],
        recording_name=first.recording_filename,
        time_condition_name=name,
        reloaded=reloaded,
        closure_count=len(closures),
    )


def _existing_closures(ext: int) -> list[time_conditions.Closure]:
    """Current closures on a managed TC as Closure objects (recordings preserved)."""
    tc = time_conditions.get_schedule(ext)
    if not tc:
        return []
    out = []
    for c in tc.get("closures", []):
        if not (c.get("start") and c.get("end")):
            continue
        out.append(time_conditions.Closure(
            label=c.get("label") or "closure", start=c["start"], end=c["end"],
            closed_action=c.get("closed_action", "voicemail"),
            recording_filename=c.get("recording_filename", ""),
            reason=c.get("reason", ""),
        ))
    return out


def apply_schedule(req: ScheduleRequest, open_destination: str) -> ScheduleResult:
    """Add or update a single closure (by label) within the time condition on the
    target extension, preserving any other closures already there."""
    ext = _resolve_extension(req.extension)
    existing = time_conditions.get_schedule(ext)
    tc_name = (existing.get("label") if existing else None) or req.label

    phrase, rec_name, rec_filename = _synthesize_recording(ext, req)
    new_closure = time_conditions.Closure(
        label=req.label, start=req.start, end=req.end,
        closed_action=req.closed_action, recording_filename=rec_filename,
        reason=req.reason or "",
    )
    closures = [c for c in _existing_closures(ext) if c.label != req.label]
    closures.append(new_closure)

    name = time_conditions.upsert_time_condition(
        ext, tc_name, closures, open_destination=open_destination,
    )
    reloaded = xmlrpc_client.reloadxml()
    return ScheduleResult(
        extension=ext,
        label=tc_name,
        phrase_text=phrase.text,
        recording_name=rec_name,
        time_condition_name=name,
        reloaded=reloaded,
        closure_count=len(closures),
    )


def adopt_schedule(dialplan_uuid: str, req: ScheduleRequest, open_destination: str) -> ScheduleResult:
    """Admin-only: convert an existing FusionPBX time condition into a managed schedule."""
    tc = time_conditions.get_time_condition(dialplan_uuid)  # verifies + gives extension
    ext = tc["extension"]

    phrase, rec_name, rec_filename = _synthesize_recording(ext, req)

    closure = time_conditions.Closure(
        label=req.label, start=req.start, end=req.end,
        closed_action=req.closed_action, recording_filename=rec_filename,
        reason=req.reason or "",
    )
    time_conditions.adopt_time_condition(
        dialplan_uuid, req.label, [closure], open_destination=open_destination,
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
            f"{GENERATED_PREFIX}ivr_{ext}_{_slug(req.name)}", wav, description=req.name
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


def recycle_ivr(extension: int, *, actor: str) -> str | None:
    """Free an IVR's extension for reuse, logging its previous use first.

    Records the extension's prior IVR name/use in the recycle ledger, then
    deletes the IVR (removing its dialplan so the pool number frees up). Returns
    the previous IVR name, or None if there was no managed IVR on that number."""
    ivr = ivr_menus.get_ivr(extension)
    if not ivr:
        return None
    # log the previous use BEFORE freeing, so the record survives even if the
    # number is immediately reallocated
    recycle.record(extension, kind="ivr", name=ivr["name"], actor=actor)
    ivr_menus.delete_ivr(extension)  # frees the dialplan
    xmlrpc_client.reloadxml()
    return ivr["name"]


def list_recycled() -> list[dict]:
    """Recycle-ledger entries (newest first), flagged with whether the extension
    is currently free to reuse."""
    free = _free_pool_numbers()
    return [{**e, "available": e["extension"] in free} for e in recycle.entries()]


def _free_pool_numbers() -> set[int]:
    return set(extensions.POOL) - extensions.used_extensions()


def list_destinations() -> list[dict]:
    from app.fpbx import destinations
    return destinations.list_destinations()


def list_recordings() -> list[dict]:
    return recordings.list_recordings()


def list_phrases() -> list[dict]:
    """Managed greeting/phrase recordings, flagged for whether the app generated
    them via TTS (GENERATED_PREFIX) vs. an adopted/other managed recording."""
    items = recordings.list_managed()
    for it in items:
        it["generated"] = it["name"].startswith(GENERATED_PREFIX)
    return items


def delete_phrase(name: str) -> bool:
    """Admin: delete a managed phrase recording (and its stored audio)."""
    return recordings.delete_recording(name)


def list_inbound_destinations() -> list[dict]:
    return inbound_routes.list_inbound_destinations()


# ---- one-flow call flow: inbound route -> time condition -> IVR ----
def build_call_flow(
    inbound_did: str | None, schedule_req: ScheduleRequest, ivr_req: IvrRequest,
    *, confirm_overwrite: bool = False,
) -> CallFlowResult:
    """Create the IVR, a schedule whose open destination routes into it, and
    (optionally) point an existing inbound route (DID) at the schedule."""
    # safeguard first, before creating anything, so a refused overwrite doesn't
    # leave an orphaned IVR/schedule behind
    if inbound_did and not confirm_overwrite and inbound_routes.is_foreign_route(inbound_did):
        raise time_conditions.NotManaged(
            f"inbound route for {inbound_did!r} exists and is not managed by this app"
        )

    ivr = create_ivr(ivr_req)                                  # -> IVR extension
    sched = apply_schedule(schedule_req, str(ivr.extension))   # open dest = IVR
    inbound_name = None
    reloaded = sched.reloaded
    if inbound_did:
        inbound_name = inbound_routes.upsert_inbound(
            inbound_did, str(sched.extension), confirm_overwrite=confirm_overwrite
        )
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
