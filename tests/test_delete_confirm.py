"""Deletion of any resource must carry an affirmative confirmation token; a
POST without it is refused server-side (not just by the browser prompt)."""
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
        '{"ops":["manage_users","manage_schedules"]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def _login(c):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    c.post("/auth/login", data={"username": "ops", "password": "pw"}, follow_redirects=False)


@pytest.mark.parametrize("path", ["/schedules/9550/delete", "/ivrs/9560/delete"])
def test_delete_without_confirm_is_refused(client, monkeypatch, path):
    called = []
    monkeypatch.setattr(routes, "delete_schedule", lambda ext: called.append(ext))
    monkeypatch.setattr(routes, "delete_ivr", lambda ext: called.append(ext))
    with client as c:
        _login(c)
        r = c.post(path, follow_redirects=False)
        assert r.status_code == 400
        assert called == []  # nothing deleted


@pytest.mark.parametrize("path,ext", [("/schedules/9550/delete", 9550), ("/ivrs/9560/delete", 9560)])
def test_delete_with_confirm_proceeds(client, monkeypatch, path, ext):
    called = []
    monkeypatch.setattr(routes, "delete_schedule", lambda e: called.append(e))
    monkeypatch.setattr(routes, "delete_ivr", lambda e: called.append(e))
    with client as c:
        _login(c)
        r = c.post(path, data={"confirm": "yes"}, follow_redirects=False)
        assert r.status_code == 303
        assert called == [ext]
