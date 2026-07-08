"""Under an external backend (fpbx), local administrators still authenticate
against the local DB from the primary login form; the /admin/auth page can list
FusionPBX groups for the mapping builder."""
import pytest
from starlette.testclient import TestClient

from app.auth import authz, backend, local
from app.config import settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "fpbx")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "authz_group_permissions",
                        '{"agents":["manage_schedules"],"local-admins":["manage_users"]}')
    authz._group_map.cache_clear()
    # the external backend never matches here — force the local-admin fallback path
    monkeypatch.setattr(backend, "password_login", lambda u, p: None)
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_local_admin_logs_in_under_fpbx_backend(client):
    local.create_user("admin@local", "pw", is_admin=True)
    with client as c:
        r = c.post("/auth/login", data={"username": "admin@local", "password": "pw"},
                   follow_redirects=False)
        # authenticated locally -> proceeds to MFA (not the 401 invalid-credentials page)
        assert r.status_code == 303
        assert r.headers["location"] == "/auth/mfa"


def test_local_non_admin_gets_no_fallback(client):
    local.create_user("agent", "pw")  # not an admin
    with client as c:
        r = c.post("/auth/login", data={"username": "agent", "password": "pw"},
                   follow_redirects=False)
        assert r.status_code == 401  # fallback is admins-only


def test_wrong_local_admin_password_still_fails(client):
    local.create_user("admin@local", "pw", is_admin=True)
    with client as c:
        r = c.post("/auth/login", data={"username": "admin@local", "password": "nope"},
                   follow_redirects=False)
        assert r.status_code == 401


def test_fpbx_groups_route_requires_admin(client):
    # non-logged-in -> redirected to login (never leaks the group list)
    with client as c:
        assert c.get("/admin/auth/fpbx-groups", follow_redirects=False).status_code == 307
