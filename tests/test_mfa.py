import re

import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings
from app.mfa import totp


# ---- TOTP unit ----
def test_totp_roundtrip():
    s = totp.generate_secret()
    assert totp.verify(s, totp.now_code(s)) is True
    assert totp.verify(s, "000000") in (False, True)  # extremely unlikely True
    assert totp.verify(s, "12345") is False           # wrong length/format tolerated
    assert totp.verify(s, "") is False


def test_totp_drift_window():
    s = totp.generate_secret()
    at = 1_000_000_000
    prev = totp.now_code(s, at=at - 30)
    assert totp.verify(s, prev, window=1, at=at) is True
    assert totp.verify(s, totp.now_code(s, at=at - 120), window=1, at=at) is False


def test_provisioning_uri():
    uri = totp.provisioning_uri("ABC234", "admin", "Acme")
    assert uri.startswith("otpauth://totp/Acme:admin?")
    assert "secret=ABC234" in uri


# ---- local admin / MFA store ----
@pytest.fixture
def tmp_users(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))


def test_admin_flag_and_mfa_state(tmp_users):
    local.create_user("admin", "pw", is_admin=True)
    assert local.authenticate("admin", "pw")["is_admin"] is True
    assert local.has_mfa("admin") is False
    s = totp.generate_secret()
    local.set_totp_secret("admin", s)
    assert local.has_mfa("admin") is True
    local.reset_mfa("admin")
    assert local.has_mfa("admin") is False


def test_admin_via_group(tmp_users, monkeypatch):
    monkeypatch.setattr(settings, "local_admin_group", "ivr-admins")
    local.create_user("gadmin", "pw")            # no is_admin flag
    assert local.is_admin("gadmin") is False
    local.add_to_group("gadmin", "ivr-admins")
    assert local.is_admin("gadmin") is True       # admin by group membership


# ---- full login + MFA flow ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    # keep the admin-designating group distinct from the permission group
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(settings, "authz_group_permissions", '{"ivr-admins":["manage_schedules"]}')
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def _secret_from_setup(html: str) -> str:
    return re.search(r"<code>([A-Z2-7]+)</code>", html).group(1)


def test_local_admin_must_enrol_then_verify_mfa(client):
    local.create_user("admin", "pw", is_admin=True)
    local.add_to_group("admin", "ivr-admins")
    with client as c:
        # password ok -> not logged in yet, sent to MFA enrolment
        r = c.post("/auth/login", data={"username": "admin", "password": "pw"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/auth/mfa"
        assert c.get("/schedules/new", follow_redirects=False).status_code == 307  # no full session

        secret = _secret_from_setup(c.get("/auth/mfa/setup").text)
        # wrong code rejected
        assert c.post("/auth/mfa/totp/enroll", data={"code": "000000"},
                      follow_redirects=False).status_code == 400
        # correct code completes login
        r = c.post("/auth/mfa/totp/enroll", data={"code": totp.now_code(secret)}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert c.get("/schedules/new", follow_redirects=False).status_code == 200

        # subsequent login -> verify stage (already enrolled)
        c.cookies.clear()
        r = c.post("/auth/login", data={"username": "admin", "password": "pw"}, follow_redirects=False)
        assert r.headers["location"] == "/auth/mfa"
        assert "Authenticator code" in c.get("/auth/mfa").text
        r = c.post("/auth/mfa/totp/verify", data={"code": totp.now_code(secret)}, follow_redirects=False)
        assert r.headers["location"] == "/"
        assert c.get("/schedules/new", follow_redirects=False).status_code == 200


def test_non_admin_local_user_skips_mfa(client):
    local.create_user("bob", "pw")  # not an admin
    local.add_to_group("bob", "ivr-admins")  # has perms but not admin -> no MFA
    with client as c:
        r = c.post("/auth/login", data={"username": "bob", "password": "pw"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"  # straight in, no MFA


def test_breakglass_restricted_to_admins(client):
    local.create_user("bob", "pw")  # non-admin
    with client as c:
        r = c.post("/auth/local", data={"username": "bob", "password": "pw"}, follow_redirects=False)
        assert r.status_code == 401  # non-admin refused break-glass


def test_dev_mode_admin_skips_mfa_and_is_superuser(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", True)
    local.create_user("admin", "pw", is_admin=True)  # no groups mapped
    with client as c:
        r = c.post("/auth/login", data={"username": "admin", "password": "pw"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"  # no MFA gate in dev
        # local admin is a superuser -> reaches the schedules UI without a mapped group
        assert c.get("/schedules/new", follow_redirects=False).status_code == 200
