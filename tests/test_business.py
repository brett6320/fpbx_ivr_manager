import pytest
from starlette.testclient import TestClient

from app import business
from app.auth import authz, local
from app.config import settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "business_config_file", str(tmp_path / "business.json"))
    monkeypatch.setattr(settings, "app_org_name", "Fallback Org")


def test_save_load_roundtrip(store):
    business.save("Acme Co", {"greeting": "Hi", "": "blank name ignored"})
    assert business.business_name() == "Acme Co"
    assert business.templates() == {"greeting": "Hi"}


def test_business_name_falls_back(store):
    assert business.business_name() == "Fallback Org"


def test_named_placeholder_substitution(store):
    business.save("Acme Co", {"greeting": "Hello", "closing": "Have a nice day"})
    out = business.render("{greeting}, thanks for calling {business_name}. {closing}. {unknown}")
    assert out == "Hello, thanks for calling Acme Co. Have a nice day. {unknown}"


def test_nested_templating(store):
    business.save("Acme Co", {
        "opener": "Thank you for calling {business_name}",
        "closing": "Have a nice day",
        "full": "{opener}. {closing}.",
    })
    assert business.render("{full}") == "Thank you for calling Acme Co. Have a nice day."


def test_cycle_is_guarded(store):
    business.save("Acme Co", {"a": "{b}", "b": "{a}"})
    out = business.render("{a}")  # must not recurse forever
    assert "{a}" in out or "{b}" in out


def test_placeholder_keys(store):
    business.save("Acme Co", {"greeting": "x", "closing": "y"})
    keys = business.placeholder_keys()
    assert "{business_name}" in keys and "{greeting}" in keys and "{closing}" in keys


# ---- route gating + dynamic-row save ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "business_config_file", str(tmp_path / "business.json"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"ops":["manage_users","manage_schedules"],"editors":["manage_schedules"]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_admin_business_requires_manage_users_and_saves_dynamic_rows(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        assert c.get("/admin/business", follow_redirects=False).status_code == 403
        c.cookies.clear()
        c.post("/auth/login", data={"username": "ops", "password": "pw"}, follow_redirects=False)
        r = c.get("/admin/business", follow_redirects=False)
        assert r.status_code == 200 and "Business profile" in r.text
        # dynamically-added rows arrive with sparse indices
        r2 = c.post(
            "/admin/business",
            data={"business_name": "Acme", "tmpl_name_0": "greeting", "tmpl_value_0": "Hi",
                  "tmpl_name_5": "closing", "tmpl_value_5": "Bye"},
            follow_redirects=False,
        )
        assert r2.status_code == 303
        assert business.business_name() == "Acme"
        assert business.templates() == {"greeting": "Hi", "closing": "Bye"}
