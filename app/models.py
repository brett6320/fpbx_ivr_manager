"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class ScheduleRequest(BaseModel):
    """One closure. A single closure can stand alone as a schedule, or several
    can be grouped under one time condition (see TimeConditionRequest)."""
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


# A closure is exactly the shape of a ScheduleRequest (label + window + reason +
# closed action); the extension is carried by the containing time condition.
Closure = ScheduleRequest


class TimeConditionRequest(BaseModel):
    """A time condition holding one or more closures on a single extension.

    Callers hitting the extension are matched against each closure (most specific
    window first); if none match, they go to open_destination."""
    name: str                        # the time condition's name, e.g. "TC-Main"
    open_destination: str            # where callers go when no closure is active
    closures: list[Closure]
    extension: int | None = None     # None = auto-allocate from the pool

    @field_validator("closures")
    @classmethod
    def _at_least_one(cls, v: list[Closure]):
        if not v:
            raise ValueError("a time condition needs at least one closure")
        return v


class ScheduleResult(BaseModel):
    extension: int
    label: str
    phrase_text: str
    recording_name: str
    time_condition_name: str
    reloaded: bool
    closure_count: int = 1


class IvrOption(BaseModel):
    digits: str                  # e.g. "0"
    destination: str             # FreeSWITCH action, e.g. "transfer 2000 XML domain"


class IvrRequest(BaseModel):
    name: str
    extension: int | None = None            # None = auto-allocate from the pool
    greeting_text: str | None = None        # synthesize via TTS
    greeting_recording: str | None = None   # or an existing recording filename
    timeout_destination: str                # action string for the IVR timeout/exit
    options: list[IvrOption] = []

    @field_validator("timeout_destination")
    @classmethod
    def _timeout_required(cls, v: str):
        if not v or not v.strip():
            raise ValueError("a timeout destination is required")
        return v


class IvrResult(BaseModel):
    extension: int
    name: str
    ivr_menu_uuid: str
    option_count: int
    reloaded: bool


class CallFlowResult(BaseModel):
    inbound_number: str | None
    inbound_name: str | None
    schedule_extension: int
    ivr_extension: int
    ivr_name: str
    option_count: int
    reloaded: bool
