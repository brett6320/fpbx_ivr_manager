"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class ScheduleRequest(BaseModel):
    label: str                    # human name e.g. "July 4th Holiday"
    start: datetime               # local time of the FusionPBX domain
    end: datetime
    reason: str | None = None     # optional, folded into the phrase
    extension: int | None = None  # if None, auto-allocate from the pool
    closed_action: str = "voicemail"  # voicemail | hangup

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: datetime, info):
        start = info.data.get("start")
        if start and v <= start:
            raise ValueError("end must be after start")
        return v


class ScheduleResult(BaseModel):
    extension: int
    label: str
    phrase_text: str
    recording_name: str
    time_condition_name: str
    reloaded: bool
