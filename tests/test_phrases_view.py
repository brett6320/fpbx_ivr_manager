import pytest
from starlette.testclient import TestClient

from app import service
from app.auth import authz, local
from app.config import settings
from app.web import routes


def test_create_phrase_synthesizes_prefixed_recording(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.google_tts, "synthesize", lambda t: captured.setdefault("text", t) or b"RIFF")
    monkeypatch.setattr(service.recordings, "upsert_recording",
                        lambda name, wav, description="": captured.update(name=name, desc=description) or f"{name}.wav")
    monkeypatch.setattr(service.business, "render", lambda t: t.replace("{business_name}", "Acme"))
    out = service.create_phrase("Main Greeting", "Hi from {business_name}")
    assert out["name"] == "ivrmgr_phrase_main_greeting"
    assert out["filename"] == "ivrmgr_phrase_main_greeting.wav"
    assert captured["text"] == "Hi from Acme"       # placeholders resolved before TTS
    assert captured["desc"] == "Main Greeting"


def test_create_phrase_rejects_empty(monkeypatch):
    monkeypatch.setattr(service.business, "render", lambda t: t)
    with pytest.raises(ValueError):
        service.create_phrase("", "text")
    with pytest.raises(ValueError):
        service.create_phrase("Name", "   ")


def test_list_phrases_flags_generated(monkeypatch):
    monkeypatch.setattr(service.recordings, "list_managed", lambda: [
        {"name": "ivrmgr_schedule_9550_july", "filename": "a.wav", "description": "July"},
        {"name": "front_desk_greeting", "filename": "b.wav", "description": "Adopted"},
    ])
    out = {p["name"]: p["generated"] for p in service.list_phrases()}
    assert out == {"ivrmgr_schedule_9550_july": True, "front_desk_greeting": False}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"ops":["manage_users","manage_schedules"],"editors":["manage_schedules"]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def _login(c, u):
    c.cookies.clear()
    c.post("/auth/login", data={"username": u, "password": "pw"}, follow_redirects=False)


def _phrases(monkeypatch):
    monkeypatch.setattr(routes, "list_phrases", lambda: [
        {"name": "ivrmgr_ivr_9560_main", "filename": "m.wav",
         "description": "Main menu", "generated": True},
    ])


def test_editor_can_create_phrase(client, monkeypatch):
    _phrases(monkeypatch)
    made = []
    monkeypatch.setattr(routes, "create_phrase",
                        lambda label, text: made.append((label, text)) or {"name": "ivrmgr_phrase_x"})
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        assert c.get("/phrases/new", follow_redirects=False).status_code == 200
        r = c.post("/phrases", data={"label": "Greeting", "text": "hi"}, follow_redirects=False)
        assert r.status_code == 303
        assert made == [("Greeting", "hi")]


def test_editor_can_view_but_not_delete(client, monkeypatch):
    _phrases(monkeypatch)
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        r = c.get("/phrases", follow_redirects=False)
        assert r.status_code == 200
        assert "ivrmgr_ivr_9560_main" in r.text
        assert "Main menu" in r.text
        # no delete control for non-admins, and the endpoint is admin-only
        assert "/delete" not in r.text
        assert c.post("/phrases/ivrmgr_ivr_9560_main/delete",
                      data={"confirm": "yes"}, follow_redirects=False).status_code == 403


def test_admin_delete_requires_confirmation(client, monkeypatch):
    _phrases(monkeypatch)
    deleted = []
    monkeypatch.setattr(routes, "delete_phrase", lambda name: deleted.append(name) or True)
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        assert 'action="/phrases/ivrmgr_ivr_9560_main/delete"' in c.get("/phrases").text
        # without confirm -> refused, nothing deleted
        assert c.post("/phrases/ivrmgr_ivr_9560_main/delete",
                      follow_redirects=False).status_code == 400
        assert deleted == []
        # with confirm -> deleted
        r = c.post("/phrases/ivrmgr_ivr_9560_main/delete",
                   data={"confirm": "yes"}, follow_redirects=False)
        assert r.status_code == 303
        assert deleted == ["ivrmgr_ivr_9560_main"]
