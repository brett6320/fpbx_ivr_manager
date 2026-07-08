"""Orchestrate a schedule: phrase -> TTS -> recording -> time condition -> reload."""
from __future__ import annotations

import re

from app.config import settings
from app.fpbx import extensions, recordings, time_conditions, xmlrpc_client
from app.models import ScheduleRequest, ScheduleResult
from app.phrases.builder import build_phrase
from app.tts import google_tts


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "schedule"


def preview_phrase(req: ScheduleRequest) -> str:
    return build_phrase(
        req.start, req.end, org_name=settings.app_org_name, reason=req.reason
    ).text


def _synthesize_recording(ext: int, req: ScheduleRequest):
    """Build the greeting and store it as a recording. Returns (phrase, rec_name, filename)."""
    phrase = build_phrase(req.start, req.end, org_name=settings.app_org_name, reason=req.reason)
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
