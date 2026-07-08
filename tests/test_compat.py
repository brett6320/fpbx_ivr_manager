import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings
from app.fpbx import compat


def _full_schema() -> dict:
    return {t: set(cols) for t, cols in compat.REQUIRED_SCHEMA.items()}


def test_diff_missing_all_present():
    d = compat.diff_missing(_full_schema())
    assert d == {"missing_tables": [], "missing_columns": []}


def test_diff_missing_table_absent():
    existing = _full_schema()
    del existing["v_recordings"]
    d = compat.diff_missing(existing)
    assert d["missing_tables"] == ["v_recordings"]
    assert d["missing_columns"] == []


def test_diff_missing_column_absent():
    existing = _full_schema()
    existing["v_dialplans"] = existing["v_dialplans"] - {"app_uuid"}
    d = compat.diff_missing(existing)
    assert d["missing_columns"] == ["v_dialplans.app_uuid"]
    assert d["missing_tables"] == []


def test_supported_versions_span_baseline_to_current():
    assert compat.SUPPORTED_VERSIONS[0] == "4.5"
    assert compat.SUPPORTED_VERSIONS[-1] == "5.5"
    # every subsequent major 5.x is enumerated
    assert {"5.0", "5.1", "5.2", "5.3", "5.4"}.issubset(set(compat.SUPPORTED_VERSIONS))


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


def test_admin_compat_requires_manage_users(client, monkeypatch):
    monkeypatch.setattr(
        compat, "check_schema",
        lambda: {"ok": True, "checked_tables": ["v_dialplans"], "supported": compat.SUPPORTED_RANGE,
                 "missing_tables": [], "missing_columns": []},
    )
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    local.create_user("viewer", "pw")
    local.add_to_group("viewer", "viewers")
    with client as c:
        c.post("/auth/login", data={"username": "viewer", "password": "pw"}, follow_redirects=False)
        assert c.get("/admin/compat", follow_redirects=False).status_code == 403
        c.cookies.clear()
        c.post("/auth/login", data={"username": "ops", "password": "pw"}, follow_redirects=False)
        r = c.get("/admin/compat", follow_redirects=False)
        assert r.status_code == 200
        assert r.json()["ok"] is True
