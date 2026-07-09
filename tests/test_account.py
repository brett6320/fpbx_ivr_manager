import pytest
from starlette.testclient import TestClient

from app.auth import authz, backend, local
from app.config import settings


def _client(monkeypatch, tmp_path, auth_backend="local"):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", auth_backend)
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "authz_group_permissions", '{"users":["manage_schedules"]}')
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


# ---- local user: editable ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    return _client(monkeypatch, tmp_path, "local")


def _login(c, u, p):
    c.post("/auth/login", data={"username": u, "password": p}, follow_redirects=False)


def test_local_user_can_view_and_edit(client):
    local.create_user("alice", "pw", display_name="Alice")
    local.add_to_group("alice", "users")
    with client as c:
        _login(c, "alice", "pw")
        r = c.get("/account", follow_redirects=False)
        assert r.status_code == 200
        assert "Alice" in r.text and "Change password" in r.text
        # update display name + password
        r2 = c.post("/account", data={
            "display_name": "Alice B", "current_password": "pw",
            "new_password": "newpw", "confirm_password": "newpw",
        }, follow_redirects=False)
        assert r2.status_code == 303
    assert local.get_user("alice")["display_name"] == "Alice B"
    assert local.authenticate("alice", "newpw") is not None
    assert local.authenticate("alice", "pw") is None


def test_wrong_current_password_rejected(client):
    local.create_user("alice", "pw", display_name="Alice")
    local.add_to_group("alice", "users")
    with client as c:
        _login(c, "alice", "pw")
        r = c.post("/account", data={
            "display_name": "Alice", "current_password": "WRONG",
            "new_password": "x", "confirm_password": "x",
        }, follow_redirects=False)
        assert r.status_code == 400 and "incorrect" in r.text
    assert local.authenticate("alice", "pw") is not None  # unchanged


def test_password_mismatch_rejected(client):
    local.create_user("alice", "pw", display_name="Alice")
    local.add_to_group("alice", "users")
    with client as c:
        _login(c, "alice", "pw")
        r = c.post("/account", data={
            "display_name": "Alice", "current_password": "pw",
            "new_password": "a", "confirm_password": "b",
        }, follow_redirects=False)
        assert r.status_code == 400 and "do not match" in r.text


def test_account_requires_login(client):
    with client as c:
        assert c.get("/account", follow_redirects=False).status_code == 307


# ---- passkey management (local accounts) ----
def test_local_credential_store_scopes_delete_and_tracks_created(client):
    local.create_user("alice", "pw", display_name="Alice")
    local.create_user("mallory", "pw", display_name="Mallory")
    local.add_credential("alice", "cred-a", "pubkey", 0, label="Laptop")
    local.add_credential("mallory", "cred-m", "pubkey", 0, label="Phone")

    creds = local.get_credentials("alice")
    assert [c["label"] for c in creds] == ["Laptop"]
    assert creds[0]["created_at"]  # stamped on registration

    # a user cannot delete someone else's passkey
    assert local.delete_credential("alice", "cred-m") is False
    assert local.get_credentials("mallory")  # still there
    # but can delete their own
    assert local.delete_credential("alice", "cred-a") is True
    assert local.get_credentials("alice") == []


def test_account_lists_and_removes_passkeys(client):
    local.create_user("alice", "pw", display_name="Alice")
    local.add_to_group("alice", "users")
    local.add_credential("alice", "cred-a", "pubkey", 0, label="MacBook")
    with client as c:
        _login(c, "alice", "pw")
        r = c.get("/account", follow_redirects=False)
        assert r.status_code == 200
        assert "Passkeys" in r.text and "MacBook" in r.text
        # remove it
        r2 = c.post("/account/passkeys/delete", data={"credential_id": "cred-a"},
                    follow_redirects=False)
        assert r2.status_code == 303
    assert local.get_credentials("alice") == []


def test_passkey_endpoints_reject_external_identity(tmp_path, monkeypatch):
    c = _client(monkeypatch, tmp_path, "fpbx")
    ext = {"name": "Bob Ext", "email": "bob", "oid": "u-9", "groups": ["superadmin"]}
    monkeypatch.setattr(backend, "password_login", lambda u, p: ext)
    with c:
        c.post("/auth/login", data={"username": "bob", "password": "pw"}, follow_redirects=False)
        assert c.post("/account/passkeys/register-options",
                      follow_redirects=False).status_code == 403
        assert c.post("/account/passkeys/delete", data={"credential_id": "x"},
                      follow_redirects=False).status_code == 403


# ---- external identity: read-only ----
def test_external_user_is_read_only(tmp_path, monkeypatch):
    c = _client(monkeypatch, tmp_path, "fpbx")
    ext = {"name": "Bob Ext", "email": "bob", "oid": "u-9", "groups": ["superadmin"]}
    monkeypatch.setattr(backend, "password_login", lambda u, p: ext)
    with c:
        c.post("/auth/login", data={"username": "bob", "password": "pw"}, follow_redirects=False)
        r = c.get("/account", follow_redirects=False)
        assert r.status_code == 200
        assert "read-only" in r.text
        assert "FusionPBX users" in r.text      # source label
        assert "Bob Ext" in r.text and "superadmin" in r.text
        assert "Change password" not in r.text
        # editing is refused for external identities
        assert c.post("/account", data={"display_name": "x"},
                      follow_redirects=False).status_code == 403
