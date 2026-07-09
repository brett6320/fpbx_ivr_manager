"""Build a schedule greeting from timing input.

Greeting = OPENING + <body describing the window, varying by timing> + CLOSING.
The body is closure-oriented announcement wording (the app schedules office
closures), assembled from the shape of the time window:
  - full single day          -> "closed all day <weekday>, <date>"
  - multiple full days       -> "closed from <date> through <date>"
  - early close (same day)   -> "closing early at <time> on <date>"
  - late open  (same day)    -> "opening late at <time> on <date>"
  - partial window (same day)-> "closed from <time> to <time> on <date>"
  - partial across days      -> "closed from <date> <time> until <date> <time>"
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

# Business hours used to detect "full day" vs "early/late" partials.
DAY_START = time(0, 0)
DAY_END = time(23, 59)
OPEN_TIME = time(9, 0)
CLOSE_TIME = time(17, 0)

# Default fixed parts of the closure greeting. Admins can override these in the
# Business profile; when unset, these are used. {org} in the opening is filled
# with the business name. Kept here so the UI can show them as the "default".
DEFAULT_OPENING = "Thank you for calling {org}."
DEFAULT_CLOSING = (
    "We apologize for any inconvenience. Please call back during our "
    "regular business hours, or stay on the line to leave a message. Goodbye."
)


@dataclass(frozen=True)
class Phrase:
    opening: str
    body: str
    closing: str

    @property
    def text(self) -> str:
        return " ".join(p.strip() for p in (self.opening, self.body, self.closing) if p.strip())


def _fmt_date(d: datetime) -> str:
    # "Monday, July 7th"
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return d.strftime(f"%A, %B {day}{suffix}")


def _fmt_time(d: datetime) -> str:
    # "3:00 PM" (no leading zero on hour)
    return d.strftime("%I:%M %p").lstrip("0")


def _is_midnight(t: time) -> bool:
    return t == DAY_START


def _is_end_of_day(t: time) -> bool:
    return t >= DAY_END


def describe_window(start: datetime, end: datetime) -> str:
    """Return the varying middle sentence for a time window [start, end]."""
    if end <= start:
        raise ValueError("end must be after start")

    same_day = start.date() == end.date()
    full_start = _is_midnight(start.time())
    full_end = _is_end_of_day(end.time())

    if same_day:
        if full_start and full_end:
            return f"We are closed all day {_fmt_date(start)}."
        if full_start and not full_end:
            # opens the day closed, reopens later -> late open
            return f"We are opening late at {_fmt_time(end)} on {_fmt_date(start)}."
        if not full_start and full_end:
            # open then close for the rest of the day -> early close
            return f"We are closing early at {_fmt_time(start)} on {_fmt_date(start)}."
        return (
            f"We are closed from {_fmt_time(start)} to {_fmt_time(end)} "
            f"on {_fmt_date(start)}."
        )

    # multi-day
    if full_start and full_end:
        return f"We are closed from {_fmt_date(start)} through {_fmt_date(end)}."
    return (
        f"We are closed from {_fmt_date(start)} at {_fmt_time(start)} "
        f"until {_fmt_date(end)} at {_fmt_time(end)}."
    )


def reopen_sentence(reopen: datetime | None, has_time: bool = False) -> str:
    """'We will reopen on <date>[ at <time>].' — empty when no reopen date."""
    if reopen is None:
        return ""
    s = f"We will reopen on {_fmt_date(reopen)}"
    if has_time:
        s += f" at {_fmt_time(reopen)}"
    return s + "."


def build_phrase(
    start: datetime,
    end: datetime,
    *,
    org_name: str,
    reason: str | None = None,
    opening: str | None = None,
    closing: str | None = None,
    reopen: datetime | None = None,
    reopen_has_time: bool = False,
) -> Phrase:
    """Assemble opening + body + closing greeting.

    opening/closing override the fixed parts (from the Business profile); when
    None the defaults are used. The opening's {org} placeholder is filled with
    org_name. An optional reopen date/time is appended to the body.
    """
    opening = (opening or DEFAULT_OPENING).replace("{org}", org_name)
    body = describe_window(start, end)
    if reason:
        body = f"{body} This closure is for {reason}."
    reopen_txt = reopen_sentence(reopen, reopen_has_time)
    if reopen_txt:
        body = f"{body} {reopen_txt}"
    closing = closing or DEFAULT_CLOSING
    return Phrase(opening=opening, body=body, closing=closing)
