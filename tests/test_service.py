from datetime import datetime
from types import SimpleNamespace

import pytest

from app import service
from app.models import ScheduleRequest
from app.service import _slug, preview_phrase


def _req(ext):
    return ScheduleRequest(
        label="x", start=datetime(2026, 7, 7, 0, 0), end=datetime(2026, 7, 7, 23, 59),
        extension=ext,
    )


def test_apply_edits_existing_managed_record_outside_pool(monkeypatch):
    # an adopted time condition on 3000 (outside 9550-9599) must remain editable
    monkeypatch.setattr(service.time_conditions, "get_schedule", lambda e: {"extension": e})
    monkeypatch.setattr(service, "_synthesize_recording",
                        lambda ext, req: (SimpleNamespace(text="hi"), "rec", "rec.wav"))
    monkeypatch.setattr(service.time_conditions, "upsert_time_condition",
                        lambda *a, **k: "after_hours")
    monkeypatch.setattr(service.xmlrpc_client, "reloadxml", lambda: True)
    res = service.apply_schedule(_req(3000), "2000")
    assert res.extension == 3000


def test_apply_new_record_outside_pool_is_rejected(monkeypatch):
    # a *new* schedule on an out-of-pool number is refused (only adoption allows it)
    monkeypatch.setattr(service.time_conditions, "get_schedule", lambda e: None)
    with pytest.raises(ValueError):
        service.apply_schedule(_req(3000), "2000")


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


def test_preview_phrase_uses_configured_closure_message(monkeypatch):
    monkeypatch.setattr(service.business, "business_name", lambda: "Acme")
    monkeypatch.setattr(service.business, "closure_opening",
                        lambda: "You've reached {business_name}.")
    monkeypatch.setattr(service.business, "closure_closing", lambda: "Take care.")
    req = ScheduleRequest(
        label="Holiday", start=datetime(2026, 7, 7, 0, 0), end=datetime(2026, 7, 7, 23, 59),
    )
    text = preview_phrase(req)
    assert text.startswith("You've reached Acme.")   # {business_name} resolved
    assert text.rstrip().endswith("Take care.")


def test_schedule_request_rejects_end_before_start():
    import pytest

    with pytest.raises(ValueError):
        ScheduleRequest(
            label="x",
            start=datetime(2026, 7, 7, 12, 0),
            end=datetime(2026, 7, 7, 9, 0),
        )
