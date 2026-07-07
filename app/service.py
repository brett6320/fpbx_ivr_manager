"""Orchestrate a closure: phrase -> TTS -> recording -> time condition -> reload."""
from __future__ import annotations

import re

from app.config import settings
from app.fpbx import extensions, recordings, time_conditions, xmlrpc_client
from app.models import ClosureRequest, ClosureResult
from app.phrases.builder import build_phrase
from app.tts import google_tts


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "closure"


def preview_phrase(req: ClosureRequest) -> str:
    return build_phrase(
        req.start, req.end, org_name=settings.app_org_name, reason=req.reason
    ).text


def apply_closure(req: ClosureRequest, open_destination: str) -> ClosureResult:
    ext = extensions.validate(req.extension) if req.extension else extensions.allocate()

    phrase = build_phrase(
        req.start, req.end, org_name=settings.app_org_name, reason=req.reason
    )

    wav = google_tts.synthesize(phrase.text)
    rec_name = f"closure_{ext}_{_slug(req.label)}"
    rec_filename = recordings.upsert_recording(rec_name, wav, description=req.label)

    tc_name = time_conditions.upsert_time_condition(
        ext,
        req.label,
        (req.start, req.end),
        rec_filename,
        closed_action=req.closed_action,
        open_destination=open_destination,
    )

    reloaded = xmlrpc_client.reloadxml()

    return ClosureResult(
        extension=ext,
        label=req.label,
        phrase_text=phrase.text,
        recording_name=rec_name,
        time_condition_name=tc_name,
        reloaded=reloaded,
    )


def list_closures() -> list[dict]:
    return time_conditions.list_closures()


def get_closure(extension: int) -> dict | None:
    return time_conditions.get_closure(extension)


def delete_closure(extension: int) -> bool:
    deleted = time_conditions.delete_time_condition(extension)
    if deleted:
        xmlrpc_client.reloadxml()
    return deleted
