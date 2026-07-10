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
        for link in ("Schedules", "IVRs", "Phrases", "Sign&nbsp;out"):
            assert link in html
        assert 'href="/phrases"' in html   # non-admins can view phrases
        # editor lacks manage_users -> none of the admin-only links appear:
        # adopt, backup (import/export), users, auth, call flow, compatibility, business
        for href in ('/admin/adopt', '/admin/import', '/admin/export', '/admin/users',
                     '/admin/auth', '/flow/new', '/admin/compat', '/admin/business'):
            assert f'href="{href}"' not in html, href


def test_nav_shows_admin_links_for_manage_users(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        html = c.get("/schedules/new", follow_redirects=False).text
        for link in ("/admin/adopt", "/admin/auth", "/admin/compat", "/flow/new"):
            assert f'href="{link}"' in html


def test_nav_groups_into_scheduling_and_administration_dropdowns(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        html = c.get("/schedules/new", follow_redirects=False).text
        # both groups render as dropdowns
        assert "Scheduling" in html and "Administration" in html
        assert 'class="dropdown-menu"' in html
        # the scheduling items live inside the menu
        assert 'href="/ivrs"' in html and 'href="/phrases"' in html


def test_editor_sees_scheduling_dropdown_but_no_administration(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        html = c.get("/schedules/new", follow_redirects=False).text
        assert "Scheduling" in html          # schedule management still grouped
        assert "Administration" not in html  # no admin group for a non-admin
