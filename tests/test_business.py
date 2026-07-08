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
    business.save("Acme Co", {"Standard": "Mon-Fri, 9 to 5", "": "ignored blank name"})
    assert business.business_name() == "Acme Co"
    assert business.hours_templates() == {"Standard": "Mon-Fri, 9 to 5"}


def test_business_name_falls_back(store):
    assert business.business_name() == "Fallback Org"  # nothing saved yet


def test_placeholder_substitution(store):
    business.save("Acme Co", {"Standard": "Mon-Fri, 9 to 5", "Holiday": "Closed"})
    text = "Thanks for calling {business_name}. Our hours are {hours.Standard}. {business_hours}. {unknown}"
    out = business.render(text)
    assert "Thanks for calling Acme Co." in out
    assert "Our hours are Mon-Fri, 9 to 5." in out
    assert "Mon-Fri, 9 to 5." in out          # {business_hours} = first template
    assert "{unknown}" in out                  # unknown placeholders left intact


def test_placeholder_keys(store):
    business.save("Acme Co", {"Standard": "x"})
    keys = business.placeholder_keys()
    assert "{business_name}" in keys and "{hours.Standard}" in keys and "{business_hours}" in keys


# ---- route gating ----
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


def test_admin_business_requires_manage_users(client):
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
        assert r.status_code == 200
        assert "Business profile" in r.text and "{business_name}" in r.text
        # save via the form
        r2 = c.post("/admin/business",
                    data={"business_name": "Acme", "tmpl_name_0": "Standard", "tmpl_text_0": "9-5"},
                    follow_redirects=False)
        assert r2.status_code == 303
        assert business.business_name() == "Acme"
