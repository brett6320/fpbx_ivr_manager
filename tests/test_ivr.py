import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings
from app.fpbx import destinations, ivr_menus
from app.models import IvrRequest
from app.web import routes


# ---- destinations ----
def test_transfer_action_uses_domain():
    assert destinations.transfer_action("2000", "pbx.test") == "transfer 2000 XML pbx.test"


def test_manual_destination_valid_and_invalid():
    d = destinations.manual_destination("18005551234")
    assert d["kind"] == "manual"
    assert d["value"] == destinations.transfer_action("18005551234")
    for bad in ["", "20 00", "hi; rm", "2000 XML x"]:
        with pytest.raises(ValueError):
            destinations.manual_destination(bad)


# ---- ivr dialplan builder ----
def test_split_action():
    assert ivr_menus._split_action("transfer 2000 XML pbx.test") == ("transfer", "2000 XML pbx.test")
    assert ivr_menus._split_action("hangup") == ("hangup", "")


def test_build_ivr_dialplan_xml():
    xml = ivr_menus.build_ivr_dialplan_xml(9600, "Main Menu", "uuid-123", "transfer 2000 XML pbx.test")
    assert ivr_menus._MARKER_XML in xml
    assert 'expression="^9600$"' in xml
    assert 'data="ivr_menu_uuid=uuid-123"' in xml
    assert 'application="lua" data="ivr_menu.lua"' in xml
    assert 'application="transfer" data="2000 XML pbx.test"' in xml  # timeout/exit


def test_ivr_request_requires_timeout():
    with pytest.raises(ValueError):
        IvrRequest(name="x", timeout_destination="")


# ---- routes ----
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


def test_ivr_new_renders_for_scheduler(client, monkeypatch):
    monkeypatch.setattr(routes, "list_destinations",
                        lambda: [{"kind": "ring_group", "number": "8000", "label": "Ring group 8000 — After Hours",
                                  "value": "transfer 8000 XML pbx.test"}])
    monkeypatch.setattr(routes, "list_recordings", lambda: [])
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        r = c.get("/ivrs/new", follow_redirects=False)
        assert r.status_code == 200
        assert "New IVR menu" in r.text
        assert "After Hours" in r.text          # destination offered
        assert "Timeout destination" in r.text


def test_ivr_list_requires_manage_schedules(client, monkeypatch):
    monkeypatch.setattr(routes, "list_ivrs", lambda: [])
    local.create_user("nobody", "pw")
    local.add_to_group("nobody", "none")
    with client as c:
        c.post("/auth/login", data={"username": "nobody", "password": "pw"}, follow_redirects=False)
        assert c.get("/ivrs", follow_redirects=False).status_code == 403
