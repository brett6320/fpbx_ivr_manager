"""The app can be served under a sub-URI (BASE_PATH), not only at the root."""
import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app.config import settings
from app.web import routes

PREFIX = "/ivr-manager"


@pytest.fixture
def sub_client(monkeypatch):
    """A TestClient for a fresh app instance mounted under PREFIX, mirroring how
    main.py wires the router + session middleware but with a non-empty base path."""
    monkeypatch.setattr(settings, "base_path_raw", PREFIX, raising=False)
    assert settings.base_path == PREFIX
    # templates render internal links with this global (set to "" at import)
    monkeypatch.setitem(routes.templates.env.globals, "base_path", settings.base_path)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test", path=settings.base_path)
    app.include_router(routes.router, prefix=settings.base_path)
    return TestClient(app, follow_redirects=False)


def test_login_page_served_under_prefix(sub_client):
    r = sub_client.get(f"{PREFIX}/auth/login")
    assert r.status_code == 200
    # the login form posts back to the prefixed path, not the root
    assert f'action="{PREFIX}/auth/login"' in r.text


def test_root_paths_are_not_served_when_mounted_under_prefix(sub_client):
    assert sub_client.get("/auth/login").status_code == 404
    assert sub_client.get("/").status_code == 404


def test_protected_route_redirects_to_prefixed_login(sub_client):
    r = sub_client.get(f"{PREFIX}/")
    assert r.status_code == 307
    assert r.headers["location"] == f"{PREFIX}/auth/login"


def test_base_path_defaults_to_root(monkeypatch):
    # explicit BASE_PATH wins
    monkeypatch.setattr(settings, "base_path_raw", "/foo/", raising=False)
    assert settings.base_path == "/foo"
    # otherwise derived from APP_BASE_URL's path
    monkeypatch.setattr(settings, "base_path_raw", "", raising=False)
    monkeypatch.setattr(settings, "app_base_url", "https://host/ivr", raising=False)
    assert settings.base_path == "/ivr"
    monkeypatch.setattr(settings, "app_base_url", "https://host", raising=False)
    assert settings.base_path == ""
