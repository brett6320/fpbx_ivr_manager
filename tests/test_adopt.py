import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings
from app.web import routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"ops":["manage_users","manage_schedules"],"viewers":["manage_schedules"]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def _login(c, user):
    c.cookies.clear()
    c.post("/auth/login", data={"username": user, "password": "pw"}, follow_redirects=False)


def test_adopt_list_requires_manage_users(client, monkeypatch):
    monkeypatch.setattr(routes, "list_adoptable", lambda: [
        {"dialplan_uuid": "u1", "extension": 3000, "name": "After Hours",
         "description": "main", "enabled": True},
    ])
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    local.create_user("viewer", "pw")
    local.add_to_group("viewer", "viewers")
    with client as c:
        _login(c, "viewer")
        assert c.get("/admin/adopt", follow_redirects=False).status_code == 403
        _login(c, "ops")
        r = c.get("/admin/adopt", follow_redirects=False)
        assert r.status_code == 200
        assert "After Hours" in r.text and "3000" in r.text


def test_adopt_form_prefills_from_candidate(client, monkeypatch):
    monkeypatch.setattr(routes, "get_adoptable", lambda uuid: {
        "dialplan_uuid": uuid, "extension": 3000, "name": "After Hours", "description": "main line",
    })
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        r = c.get("/admin/adopt/u1", follow_redirects=False)
        assert r.status_code == 200
        assert "Adopt time condition" in r.text
        assert 'value="3000"' in r.text           # extension prefilled
        assert 'name="adopt_uuid" value="u1"' in r.text
        assert 'action="/admin/adopt"' in r.text


def test_adopt_form_gated(client):
    local.create_user("viewer", "pw")
    local.add_to_group("viewer", "viewers")
    with client as c:
        _login(c, "viewer")
        assert c.get("/admin/adopt/u1", follow_redirects=False).status_code == 403
