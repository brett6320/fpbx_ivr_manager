from datetime import datetime
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app import service
from app.auth import authz, local
from app.config import settings
from app.fpbx import inbound_routes
from app.models import IvrRequest, ScheduleRequest
from app.web import routes


def test_build_inbound_xml():
    xml = inbound_routes.build_inbound_xml("18005551234", "9550", "dom-1", "pbx.test")
    assert inbound_routes._MARKER_XML in xml
    assert 'expression="^18005551234$"' in xml
    assert 'call_direction=inbound' in xml
    assert 'application="transfer" data="9550 XML pbx.test"' in xml


def _reqs():
    s = ScheduleRequest(label="Biz", start=datetime(2026, 7, 7, 0, 0),
                        end=datetime(2026, 7, 7, 23, 59))
    i = IvrRequest(name="Main", timeout_destination="transfer *99100 XML pbx.test")
    return s, i


def test_build_call_flow_refuses_foreign_did_without_confirm(monkeypatch):
    monkeypatch.setattr(service.inbound_routes, "is_foreign_route", lambda did: True)
    created = []
    monkeypatch.setattr(service, "create_ivr", lambda req: created.append(1))
    s, i = _reqs()
    with pytest.raises(service.time_conditions.NotManaged):
        service.build_call_flow("1800", s, i)  # foreign + no confirm
    assert created == []  # safeguard runs BEFORE anything is created


def test_build_call_flow_overwrites_with_confirm(monkeypatch):
    monkeypatch.setattr(service.inbound_routes, "is_foreign_route", lambda did: True)
    monkeypatch.setattr(service, "create_ivr",
                        lambda req: SimpleNamespace(extension=9560, name="Main", option_count=0))
    monkeypatch.setattr(service, "apply_schedule",
                        lambda req, dest: SimpleNamespace(extension=9550, reloaded=True))
    seen = {}
    monkeypatch.setattr(service.inbound_routes, "upsert_inbound",
                        lambda did, dest, confirm_overwrite: seen.update(
                            did=did, dest=dest, confirm=confirm_overwrite) or "inbound_1800")
    monkeypatch.setattr(service.xmlrpc_client, "reloadxml", lambda: True)
    res = service.build_call_flow("1800", *_reqs(), confirm_overwrite=True)
    assert res.inbound_name == "inbound_1800"
    assert seen == {"did": "1800", "dest": "9550", "confirm": True}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"admins":["manage_users","manage_schedules"],"editors":["manage_schedules"],"none":[]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_flow_new_renders(client, monkeypatch):
    monkeypatch.setattr(routes, "list_destinations",
                        lambda: [{"kind": "voicemail", "number": "*99100", "label": "Voicemail 100",
                                  "value": "transfer *99100 XML pbx.test"}])
    monkeypatch.setattr(routes, "list_recordings", lambda: [])
    monkeypatch.setattr(routes, "list_inbound_destinations",
                        lambda: [{"did": "18005551234", "name": "MainDID",
                                  "enabled": True, "managed": False}])
    local.create_user("adm", "pw")
    local.add_to_group("adm", "admins")
    with client as c:
        c.post("/auth/login", data={"username": "adm", "password": "pw"}, follow_redirects=False)
        r = c.get("/flow/new", follow_redirects=False)
        assert r.status_code == 200
        assert "New call flow" in r.text
        assert "Inbound DID" in r.text and "Time condition" in r.text and "IVR menu" in r.text
        assert "Voicemail 100" in r.text
        # DID is a selector of existing destinations, with overwrite confirmation
        assert "18005551234 — MainDID (existing route)" in r.text
        assert 'name="confirm_overwrite"' in r.text


def test_flow_requires_admin_not_just_schedules(client):
    # call flows are admin-only: a schedules-only user is refused
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    local.create_user("nobody", "pw")
    local.add_to_group("nobody", "none")
    with client as c:
        c.post("/auth/login", data={"username": "nobody", "password": "pw"}, follow_redirects=False)
        assert c.get("/flow/new", follow_redirects=False).status_code == 403
        c.cookies.clear()
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        assert c.get("/flow/new", follow_redirects=False).status_code == 403
