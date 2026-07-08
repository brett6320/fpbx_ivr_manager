from datetime import datetime

from app.models import ScheduleRequest
from app.service import _slug, preview_phrase


def test_slug_normalizes():
    assert _slug("July 4th Holiday!") == "july_4th_holiday"
    assert _slug("  Spaces  &  Symbols  ") == "spaces_symbols"
    assert _slug("") == "schedule"
    assert _slug("---") == "schedule"


def test_preview_phrase_uses_org_and_reason():
    req = ScheduleRequest(
        label="Holiday",
        start=datetime(2026, 7, 7, 0, 0),
        end=datetime(2026, 7, 7, 23, 59),
        reason="a company holiday",
    )
    text = preview_phrase(req)
    assert text.startswith("Thank you for calling Acme.")   # APP_ORG_NAME in tests
    assert "closed all day Tuesday, July 7th" in text
    assert "This closure is for a company holiday." in text


def test_schedule_request_rejects_end_before_start():
    import pytest

    with pytest.raises(ValueError):
        ScheduleRequest(
            label="x",
            start=datetime(2026, 7, 7, 12, 0),
            end=datetime(2026, 7, 7, 9, 0),
        )
