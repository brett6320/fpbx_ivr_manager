import json

import pytest

from app.auth import authz
from app.config import settings


@pytest.fixture(autouse=True)
def _clear():
    authz._group_map.cache_clear()
    yield
    authz._group_map.cache_clear()


def _set(monkeypatch, backend, mapping):
    monkeypatch.setattr(settings, "auth_backend", backend)
    monkeypatch.setattr(settings, "authz_group_permissions", mapping)
    authz._group_map.cache_clear()


def test_fpbx_default_applies_when_unset(monkeypatch):
    _set(monkeypatch, "fpbx", "")  # unset
    assert authz.permissions_for(["superadmin"]) == {"manage_schedules", "manage_users"}
    assert authz.permissions_for(["ivr-admins"]) == {"manage_schedules", "manage_users"}
    assert authz.permissions_for(["ivr-editors"]) == {"manage_schedules"}
    assert authz.permissions_for(["admin"]) == {"manage_schedules"}
    assert authz.permissions_for(["user"]) == {"manage_schedules"}
    assert authz.permissions_for(["nobody"]) == set()


def test_fpbx_empty_braces_also_triggers_default(monkeypatch):
    _set(monkeypatch, "fpbx", "{}")
    assert authz.permissions_for(["superadmin"]) == {"manage_schedules", "manage_users"}


def test_explicit_config_overrides_default(monkeypatch):
    _set(monkeypatch, "fpbx", '{"agents":["manage_schedules"]}')
    assert authz.permissions_for(["agents"]) == {"manage_schedules"}
    # default groups no longer apply once an explicit mapping is set
    assert authz.permissions_for(["superadmin"]) == set()


def test_default_is_fpbx_only(monkeypatch):
    _set(monkeypatch, "local", "")
    assert authz.permissions_for(["superadmin"]) == set()
    assert authz.permissions_for(["ivr-admins"]) == set()


def test_group_permissions_json_reflects_default(monkeypatch):
    _set(monkeypatch, "fpbx", "")
    data = json.loads(authz.group_permissions_json())
    assert data["superadmin"] == ["manage_schedules", "manage_users"]
    assert data["user"] == ["manage_schedules"]
