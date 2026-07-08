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


def _fake_synth(monkeypatch):
    monkeypatch.setattr(service.google_tts, "synthesize", lambda t: b"RIFFxxxx")
    monkeypatch.setattr(service.recordings, "upsert_recording",
                        lambda name, wav, description="": f"{name}.wav")


def test_apply_time_condition_builds_multiple_closures(monkeypatch):
    from datetime import datetime as _dt

    from app.models import Closure, TimeConditionRequest
    _fake_synth(monkeypatch)
    monkeypatch.setattr(service.time_conditions, "get_schedule", lambda e: None)
    monkeypatch.setattr(service.extensions, "validate", lambda e: e)
    captured = {}
    monkeypatch.setattr(service.time_conditions, "upsert_time_condition",
                        lambda ext, name, closures, *, open_destination:
                        captured.update(ext=ext, name=name, closures=closures, dest=open_destination) or "schedule_9550")
    monkeypatch.setattr(service.xmlrpc_client, "reloadxml", lambda: True)

    tc = TimeConditionRequest(
        name="TC-Main", open_destination="2000", extension=9550,
        closures=[
            Closure(label="Summer", start=_dt(2026, 7, 1, 0, 0), end=_dt(2026, 7, 8, 23, 59)),
            Closure(label="July 4th", start=_dt(2026, 7, 4, 0, 0), end=_dt(2026, 7, 4, 23, 59)),
        ],
    )
    res = service.apply_time_condition(tc)
    assert res.extension == 9550 and res.closure_count == 2
    assert captured["name"] == "TC-Main" and captured["dest"] == "2000"
    assert {c.label for c in captured["closures"]} == {"Summer", "July 4th"}


def test_apply_schedule_merges_into_existing_closures(monkeypatch):
    from datetime import datetime as _dt

    _fake_synth(monkeypatch)
    existing = {
        "label": "TC-Main", "extension": 9550,
        "closures": [{"label": "Old", "start": _dt(2026, 1, 1, 0, 0),
                      "end": _dt(2026, 1, 1, 23, 59), "closed_action": "hangup",
                      "recording_filename": "old.wav", "reason": ""}],
    }
    monkeypatch.setattr(service.time_conditions, "get_schedule", lambda e: existing)
    captured = {}
    monkeypatch.setattr(service.time_conditions, "upsert_time_condition",
                        lambda ext, name, closures, *, open_destination:
                        captured.update(name=name, closures=closures) or "schedule_9550")
    monkeypatch.setattr(service.xmlrpc_client, "reloadxml", lambda: True)

    req = ScheduleRequest(label="New", start=_dt(2026, 7, 4, 0, 0),
                          end=_dt(2026, 7, 4, 23, 59), extension=9550)
    res = service.apply_schedule(req, "2000")
    # new closure added alongside the pre-existing one; TC name preserved
    assert res.closure_count == 2 and captured["name"] == "TC-Main"
    assert {c.label for c in captured["closures"]} == {"Old", "New"}


def test_summarize_closures_splits_active_and_upcoming():
    from datetime import datetime as d
    now = d(2026, 7, 7, 12, 0)
    schedules = [
        {"extension": 9550, "label": "TC-A", "closures": [
            {"label": "Now", "start": d(2026, 7, 7, 0, 0), "end": d(2026, 7, 7, 23, 59)},   # active
            {"label": "Later", "start": d(2026, 7, 20, 0, 0), "end": d(2026, 7, 20, 23, 59)},  # upcoming
            {"label": "Past", "start": d(2026, 7, 1, 0, 0), "end": d(2026, 7, 2, 0, 0)},       # excluded
        ]},
        {"extension": 9551, "label": "TC-B", "closures": [{"label": "NoDates"}]},              # skipped
    ]
    s = service.summarize_closures(schedules, now=now)
    assert [a["label"] for a in s["active"]] == ["Now"]
    assert s["active"][0]["extension"] == 9550 and s["active"][0]["tc_label"] == "TC-A"
    assert [u["label"] for u in s["upcoming"]] == ["Later"]
    assert s["now"] == now


def test_summarize_closures_sorts_and_defaults_now():
    from datetime import datetime as d
    now = d(2026, 1, 1, 0, 0)
    schedules = [{"extension": 9550, "label": "T", "closures": [
        {"label": "b", "start": d(2026, 3, 1, 0, 0), "end": d(2026, 3, 2, 0, 0)},
        {"label": "a", "start": d(2026, 2, 1, 0, 0), "end": d(2026, 2, 2, 0, 0)},
    ]}]
    s = service.summarize_closures(schedules, now=now)
    assert [u["label"] for u in s["upcoming"]] == ["a", "b"]   # soonest first
    # default now doesn't raise
    assert set(service.summarize_closures([]).keys()) == {"active", "upcoming", "now"}


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
