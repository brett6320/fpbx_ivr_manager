import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings
from app.fpbx import inbound_routes
from app.web import routes


def test_build_inbound_xml():
    xml = inbound_routes.build_inbound_xml("18005551234", "9550", "dom-1", "pbx.test")
    assert inbound_routes._MARKER_XML in xml
    assert 'expression="^18005551234$"' in xml
    assert 'call_direction=inbound' in xml
    assert 'application="transfer" data="9550 XML pbx.test"' in xml


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"editors":["manage_schedules"],"none":[]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_flow_new_renders(client, monkeypatch):
    monkeypatch.setattr(routes, "list_destinations",
                        lambda: [{"kind": "voicemail", "number": "*99100", "label": "Voicemail 100",
                                  "value": "transfer *99100 XML pbx.test"}])
    monkeypatch.setattr(routes, "list_recordings", lambda: [])
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        r = c.get("/flow/new", follow_redirects=False)
        assert r.status_code == 200
        assert "New call flow" in r.text
        assert "Inbound number" in r.text and "Time condition" in r.text and "IVR menu" in r.text
        assert "Voicemail 100" in r.text


def test_flow_requires_manage_schedules(client):
    local.create_user("nobody", "pw")
    local.add_to_group("nobody", "none")
    with client as c:
        c.post("/auth/login", data={"username": "nobody", "password": "pw"}, follow_redirects=False)
        assert c.get("/flow/new", follow_redirects=False).status_code == 403
