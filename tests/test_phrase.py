from datetime import datetime

from fastapi.templating import Jinja2Templates

from app import service
from app.models import IvrRequest, ScheduleRequest

_T = Jinja2Templates(directory="app/web/templates")


def test_generated_recording_names_carry_prefix(monkeypatch):
    captured = []
    monkeypatch.setattr(service.google_tts, "synthesize", lambda t: b"RIFFxxxx")
    monkeypatch.setattr(service.recordings, "upsert_recording",
                        lambda name, wav, description="": captured.append(name) or f"{name}.wav")

    # schedule greeting
    service._synthesize_recording(9550, ScheduleRequest(
        label="July 4th", start=datetime(2026, 7, 4, 0, 0), end=datetime(2026, 7, 4, 23, 59)))
    assert captured[-1].startswith(service.GENERATED_PREFIX)
    assert captured[-1] == "ivrmgr_schedule_9550_july_4th"

    # IVR greeting
    monkeypatch.setattr(service.ivr_menus, "get_ivr", lambda e: None)
    monkeypatch.setattr(service.extensions, "validate", lambda e: e)
    monkeypatch.setattr(service.ivr_menus, "upsert_ivr", lambda *a, **k: "uuid")
    monkeypatch.setattr(service.xmlrpc_client, "reloadxml", lambda: True)
    service.create_ivr(IvrRequest(name="Main Menu", extension=9560, greeting_text="hi",
                                  timeout_destination="transfer 2000 XML d"))
    assert captured[-1] == "ivrmgr_ivr_9560_main_menu"


def _render_ivr_form(recordings):
    return _T.env.get_template("ivr_form.html").render(
        request=None, user={"name": "A"}, org="Acme", pool="9550-9599",
        digits=["0"], destinations=[], recordings=recordings, placeholders=[])


def test_greeting_defaults_to_existing_phrase_when_recordings_exist():
    html = _render_ivr_form([{"name": "ivrmgr_ivr_9560_main", "filename": "ivrmgr_ivr_9560_main.wav"}])
    # existing-phrase radio is the default (checked), TTS panel hidden
    assert 'value="recording" checked' in html
    assert 'value="tts" checked' not in html
    assert 'id="g_tts" style="display:none;"' in html


def test_greeting_falls_back_to_tts_when_no_recordings():
    html = _render_ivr_form([])
    assert 'value="tts" checked' in html
    assert 'value="recording" checked' not in html
