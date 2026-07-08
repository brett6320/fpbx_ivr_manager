"""The legacy embedded admin `admin` is migrated to `admin@local` once, carrying
its groups, admin flag and passkeys — and never renames an `admin` created later."""
import sqlite3

import pytest

from app.auth import local
from app.config import settings

_SCHEMA = """
CREATE TABLE users (username TEXT PRIMARY KEY, display_name TEXT, salt TEXT NOT NULL,
                    hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, totp_secret TEXT);
CREATE TABLE user_groups (username TEXT NOT NULL, group_name TEXT NOT NULL,
                          PRIMARY KEY (username, group_name));
CREATE TABLE webauthn_credentials (credential_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                          public_key TEXT NOT NULL, sign_count INTEGER NOT NULL DEFAULT 0, label TEXT);
"""


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "users.db")
    monkeypatch.setattr(settings, "local_auth_db", path)
    return path


def _seed(path, usernames):
    """Build a pre-migration DB (no meta marker) with the given admin users."""
    con = sqlite3.connect(path)
    con.executescript(_SCHEMA)
    for u in usernames:
        con.execute("INSERT INTO users (username, display_name, salt, hash, is_admin) "
                    "VALUES (?, 'Administrator', 'aa', 'hh', 1)", (u,))
        con.execute("INSERT INTO user_groups (username, group_name) VALUES (?, 'ivr-admins')", (u,))
        con.execute("INSERT INTO webauthn_credentials (credential_id, username, public_key, sign_count, label) "
                    "VALUES (?, ?, 'pk', 0, 'k')", (f"cred-{u}", u))
    con.commit()
    con.close()


def test_legacy_admin_is_renamed_preserving_everything(db_path):
    _seed(db_path, ["admin"])
    user = local.get_user("admin@local")   # first open runs the one-time migration
    assert user is not None and user["is_admin"] is True
    assert local.get_user("admin") is None
    assert "ivr-admins" in local.groups_for_user("admin@local")
    con = sqlite3.connect(db_path)
    n = con.execute("SELECT COUNT(*) FROM webauthn_credentials WHERE username='admin@local'").fetchone()[0]
    con.close()
    assert n == 1


def test_rename_skipped_when_target_already_exists(db_path):
    _seed(db_path, ["admin", "admin@local"])
    local.list_users()   # triggers migration; target exists -> no collision/merge
    assert local.get_user("admin") is not None
    assert local.get_user("admin@local") is not None


def test_admin_created_after_migration_is_not_renamed(db_path):
    # fresh DB: first open sets the marker (no legacy admin present)
    local.create_user("admin", "pw", is_admin=True)
    assert local.get_user("admin") is not None           # kept as-is
    assert local.get_user("admin@local") is None
