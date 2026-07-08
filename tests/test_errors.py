import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings
from app.fpbx import db


# ---- schema-resilient insert ----
def test_insert_row_filters_to_existing_columns(monkeypatch):
    monkeypatch.setattr(db, "table_columns", lambda t: frozenset({"a", "b"}))
    captured = {}

    class _Cur:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

    db.insert_row(_Cur(), "v_x", {"a": 1, "b": 2, "ivr_menu_context": "x"})  # last dropped
    assert "ivr_menu_context" not in captured["sql"]
    assert captured["params"] == [1, 2]
    assert captured["sql"].startswith("INSERT INTO v_x (a, b) VALUES (%s, %s)")


# ---- styled error responses ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions", '{"editors":["manage_schedules"]}'
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_auth_redirect_is_preserved(client):
    with client as c:
        r = c.get("/ivrs", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/auth/login"


def test_permission_error_rendered_in_app_shell(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        r = c.get("/admin/adopt", headers={"accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 403
        assert "Not permitted" in r.text            # styled title
        assert "requires permission" in r.text        # the detail message
        assert 'class="nav"' in r.text                # rendered inside the themed app shell


def test_error_json_for_non_html_clients(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        r = c.get("/admin/adopt", headers={"accept": "application/json"}, follow_redirects=False)
        assert r.status_code == 403
        assert r.json()["detail"].startswith("requires permission")
