import base64
import json

import pytest
from starlette.testclient import TestClient

from app.auth import authz, config_store, local, probe
from app.config import settings


# ---- config_store ----
def test_config_store_roundtrip(tmp_path):
    p = str(tmp_path / "auth.env")
    config_store.write_managed({"AUTH_BACKEND": "ldap", "LDAP_URI": "ldaps://x"}, path=p)
    got = config_store.read_managed(p)
    assert got["AUTH_BACKEND"] == "ldap"
    assert got["LDAP_URI"] == "ldaps://x"
    # empty value removes the key
    config_store.write_managed({"LDAP_URI": ""}, path=p)
    assert "LDAP_URI" not in config_store.read_managed(p)


def test_config_store_rejects_unmanaged_keys(tmp_path):
    with pytest.raises(ValueError):
        config_store.write_managed({"APP_SECRET_KEY": "hax"}, path=str(tmp_path / "a.env"))


# ---- entra probes ----
def _jwt(payload: dict) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{b64({'alg':'none'})}.{b64(payload)}."


def test_entra_decode_token_shows_groups_and_mapping():
    token = _jwt({"name": "Ada", "groups": ["ivr-admins", "other"]})
    res = probe.entra_decode_token(token, '{"ivr-admins":["manage_schedules"]}')
    assert res["ok"] and res["unverified"]
    assert res["groups"] == ["ivr-admins", "other"]
    assert res["mapped"] == {"ivr-admins": ["manage_schedules"]}


def test_entra_probe_success(monkeypatch):
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"token_endpoint": "https://t/token", "access_token": "abc"}
    monkeypatch.setattr(probe.httpx, "get", lambda *a, **k: R())
    monkeypatch.setattr(probe.httpx, "post", lambda *a, **k: R())
    res = probe.entra_probe(tenant_id="t", client_id="c", client_secret="s", redirect_uri="https://x/cb")
    assert res["ok"] is True
    assert any(s["name"] == "validate client credentials" and s["ok"] for s in res["steps"])


def test_entra_probe_bad_secret(monkeypatch):
    class G:
        def raise_for_status(self): pass
        def json(self): return {"token_endpoint": "https://t/token"}
    class P:
        status_code = 401
        text = "bad"
        def json(self): return {"error_description": "invalid client secret"}
    monkeypatch.setattr(probe.httpx, "get", lambda *a, **k: G())
    monkeypatch.setattr(probe.httpx, "post", lambda *a, **k: P())
    res = probe.entra_probe(tenant_id="t", client_id="c", client_secret="bad")
    assert res["ok"] is False


def test_entra_probe_requires_inputs():
    assert probe.entra_probe(tenant_id="", client_id="", client_secret="")["ok"] is False


# ---- route authorization: /admin/auth needs manage_users ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")  # keep test users non-admin
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"ops":["manage_users","manage_schedules"],"viewers":["manage_schedules"]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_admin_auth_requires_manage_users(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    local.create_user("viewer", "pw")
    local.add_to_group("viewer", "viewers")
    with client as c:
        c.post("/auth/login", data={"username": "ops", "password": "pw"}, follow_redirects=False)
        assert c.get("/admin/auth", follow_redirects=False).status_code == 200
        c.cookies.clear()
        c.post("/auth/login", data={"username": "viewer", "password": "pw"}, follow_redirects=False)
        assert c.get("/admin/auth", follow_redirects=False).status_code == 403
