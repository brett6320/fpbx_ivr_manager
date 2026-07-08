import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")


def test_get_user_and_set_groups(store):
    local.create_user("alice", "pw", "Alice A", is_admin=True)
    local.set_groups("alice", ["ivr-editors", "ivr-admins", "  ", "ivr-editors"])
    u = local.get_user("alice")
    assert u["display_name"] == "Alice A" and u["is_admin"] is True
    assert set(u["groups"]) == {"ivr-editors", "ivr-admins"}
    # reconcile: drop one, add one
    local.set_groups("alice", ["ivr-admins", "ops"])
    assert set(local.get_user("alice")["groups"]) == {"ivr-admins", "ops"}


def test_set_display_name(store):
    local.create_user("bob", "pw")
    assert local.set_display_name("bob", "Bob B") is True
    assert local.get_user("bob")["display_name"] == "Bob B"


# ---- routes ----
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


def test_user_crud_requires_manage_users(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        assert c.get("/admin/users", follow_redirects=False).status_code == 403
        assert c.get("/admin/users/new", follow_redirects=False).status_code == 403


def test_full_user_crud_flow(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        # list + create form render
        assert "Users" in c.get("/admin/users").text
        assert "New user" in c.get("/admin/users/new").text
        # create
        r = c.post("/admin/users", data={
            "username": "carol", "password": "pw", "display_name": "Carol",
            "is_admin": "on", "groups": "ivr-admins, ops",
        }, follow_redirects=False)
        assert r.status_code == 303
        u = local.get_user("carol")
        assert u["is_admin"] is True and set(u["groups"]) == {"ivr-admins", "ops"}
        # edit: demote, change groups + display, no password change
        r = c.post("/admin/users/carol", data={
            "display_name": "Carol C", "groups": "ivr-editors", "password": "",
        }, follow_redirects=False)
        assert r.status_code == 303
        u = local.get_user("carol")
        assert u["is_admin"] is False and u["groups"] == ["ivr-editors"]
        assert u["display_name"] == "Carol C"
        assert local.authenticate("carol", "pw") is not None  # password unchanged
        # delete requires affirmative confirmation
        assert c.post("/admin/users/carol/delete", follow_redirects=False).status_code == 400
        assert local.get_user("carol") is not None
        assert c.post("/admin/users/carol/delete", data={"confirm": "yes"},
                      follow_redirects=False).status_code == 303
        assert local.get_user("carol") is None


def test_cannot_delete_self(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        r = c.post("/admin/users/ops/delete", data={"confirm": "yes"},
                   headers={"accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 400
        assert local.get_user("ops") is not None
