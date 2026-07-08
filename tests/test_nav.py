import pytest
from starlette.testclient import TestClient

from app.auth import authz, local
from app.config import settings


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


def _login(c, user):
    c.cookies.clear()
    c.post("/auth/login", data={"username": user, "password": "pw"}, follow_redirects=False)


def test_login_page_has_no_nav(client):
    with client as c:
        assert '<nav class="nav">' not in c.get("/auth/login").text


def test_nav_shows_main_sections(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        html = c.get("/schedules/new", follow_redirects=False).text  # no DB needed
        assert '<nav class="nav">' in html
        for link in ("Schedules", "Call&nbsp;flow", "IVRs", "Sign&nbsp;out"):
            assert link in html
        # editor lacks manage_users -> no admin links
        assert 'href="/admin/adopt"' not in html


def test_nav_shows_admin_links_for_manage_users(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        html = c.get("/schedules/new", follow_redirects=False).text
        for link in ("/admin/adopt", "/admin/auth", "/admin/compat"):
            assert f'href="{link}"' in html
