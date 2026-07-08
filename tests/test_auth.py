import pytest

from app.auth import authz, local
from app.config import settings


@pytest.fixture
def tmp_users(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    return tmp_path


def test_local_auth_roundtrip(tmp_users):
    local.create_user("alice", "s3cret", "Alice A")
    assert local.authenticate("alice", "s3cret")["name"] == "Alice A"
    assert local.authenticate("alice", "wrong") is None
    assert local.authenticate("nobody", "x") is None


def test_groups_flow(tmp_users):
    local.create_user("bob", "pw")
    local.add_to_group("bob", "ivr-editors")
    local.add_to_group("bob", "ivr-editors")  # idempotent
    assert local.groups_for_user("bob") == ["ivr-editors"]
    user = local.authenticate("bob", "pw")
    assert user["groups"] == ["ivr-editors"]
    assert local.remove_from_group("bob", "ivr-editors") is True
    assert local.groups_for_user("bob") == []


def test_permissions_from_groups_only(monkeypatch):
    monkeypatch.setattr(
        settings,
        "authz_group_permissions",
        '{"ivr-admins":["manage_schedules","manage_users"],"ivr-editors":["manage_schedules"]}',
    )
    authz._group_map.cache_clear()

    editor = {"groups": ["ivr-editors"]}
    admin = {"groups": ["ivr-admins"]}
    nobody = {"groups": []}
    unknown = {"groups": ["random-group"]}

    assert authz.has_permission(editor, authz.MANAGE_SCHEDULES) is True
    assert authz.has_permission(editor, authz.MANAGE_USERS) is False
    assert authz.user_permissions(admin) == {"manage_schedules", "manage_users"}
    assert authz.user_permissions(nobody) == set()
    assert authz.user_permissions(unknown) == set()  # unmapped group grants nothing


def test_no_groups_key_is_safe(monkeypatch):
    monkeypatch.setattr(settings, "authz_group_permissions", "{}")
    authz._group_map.cache_clear()
    assert authz.user_permissions({}) == set()


def test_local_admin_is_superuser(monkeypatch):
    # even with no group mapping, a local admin holds every permission
    monkeypatch.setattr(settings, "authz_group_permissions", "{}")
    authz._group_map.cache_clear()
    admin = {"is_admin": True, "groups": []}
    assert authz.user_permissions(admin) == set(authz.ALL_PERMISSIONS)
    assert authz.has_permission(admin, authz.MANAGE_SCHEDULES)
    assert authz.has_permission(admin, authz.MANAGE_USERS)
    # non-admin still needs a mapped group
    assert authz.user_permissions({"is_admin": False, "groups": []}) == set()
