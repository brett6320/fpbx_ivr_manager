"""Local username/password auth backend (default) + local admin store.

Users live in a small SQLite DB; passwords are PBKDF2-HMAC-SHA256 (stdlib, no
external deps). Local admins are retained and usable even when the primary
AUTH_BACKEND is an external IdP (see app.auth.local_admin) and are required to
complete MFA (TOTP or passkey) — see app.mfa.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from contextlib import contextmanager

from app.config import settings

_ITERATIONS = 210_000

# The built-in seed admin. Named with an "@local" suffix so it is unmistakably
# the app's local account, distinct from FusionPBX's own "admin" user.
EMBEDDED_ADMIN_USER = "admin@local"
LEGACY_ADMIN_USER = "admin"  # older builds seeded this; migrated on first open


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _rename_user(conn, old: str, new: str) -> bool:
    """Rename a local user across every username-keyed table, preserving groups,
    passkeys and flags. No-op unless `old` exists and `new` is free."""
    have_old = conn.execute("SELECT 1 FROM users WHERE username=?", (old,)).fetchone()
    have_new = conn.execute("SELECT 1 FROM users WHERE username=?", (new,)).fetchone()
    if not have_old or have_new:
        return False
    for table in ("users", "user_groups", "webauthn_credentials"):
        conn.execute(f"UPDATE {table} SET username=? WHERE username=?", (new, old))  # noqa: S608 - constant table names
    return True


@contextmanager
def _db():
    path = settings.local_auth_db
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            " username TEXT PRIMARY KEY,"
            " display_name TEXT,"
            " salt TEXT NOT NULL,"
            " hash TEXT NOT NULL)"
        )
        # Internal groups. Permissions attach to group names (see authz), never
        # to a username directly.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_groups ("
            " username TEXT NOT NULL,"
            " group_name TEXT NOT NULL,"
            " PRIMARY KEY (username, group_name))"
        )
        # Passkeys (WebAuthn credentials) bound to a local user.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS webauthn_credentials ("
            " credential_id TEXT PRIMARY KEY,"      # base64url
            " username TEXT NOT NULL,"
            " public_key TEXT NOT NULL,"            # base64url COSE key
            " sign_count INTEGER NOT NULL DEFAULT 0,"
            " label TEXT)"
        )
        # key/value marker table for one-time migrations
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        # additive migrations for existing DBs
        _ensure_column(conn, "users", "is_admin", "is_admin INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "totp_secret", "totp_secret TEXT")
        # ONE-TIME: rename the legacy embedded admin "admin" -> "admin@local" so it
        # is distinct from the FusionPBX "admin" (carries its groups/MFA/flags).
        # Guarded by a marker so it fires only once and never renames an "admin"
        # a user deliberately creates later.
        if not conn.execute("SELECT 1 FROM meta WHERE key='admin_renamed'").fetchone():
            _rename_user(conn, LEGACY_ADMIN_USER, EMBEDDED_ADMIN_USER)
            conn.execute("INSERT INTO meta (key, value) VALUES ('admin_renamed', '1')")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS).hex()


# ---- users ----
def create_user(
    username: str, password: str, display_name: str | None = None, is_admin: bool = False
) -> None:
    salt = secrets.token_bytes(16)
    with _db() as conn:
        conn.execute(
            "INSERT INTO users (username, display_name, salt, hash, is_admin) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET "
            "display_name=excluded.display_name, salt=excluded.salt, hash=excluded.hash",
            (username, display_name or username, salt.hex(), _hash(password, salt), int(is_admin)),
        )


def set_password(username: str, password: str) -> bool:
    salt = secrets.token_bytes(16)
    with _db() as conn:
        cur = conn.execute(
            "UPDATE users SET salt=?, hash=? WHERE username=?",
            (salt.hex(), _hash(password, salt), username),
        )
        return cur.rowcount > 0


def delete_user(username: str) -> bool:
    with _db() as conn:
        conn.execute("DELETE FROM user_groups WHERE username=?", (username,))
        conn.execute("DELETE FROM webauthn_credentials WHERE username=?", (username,))
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount > 0


def list_users() -> list[str]:
    with _db() as conn:
        return [r["username"] for r in conn.execute("SELECT username FROM users ORDER BY username")]


def user_exists(username: str) -> bool:
    with _db() as conn:
        return conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is not None


def get_user(username: str) -> dict | None:
    """Full detail for a single user, for admin management."""
    with _db() as conn:
        row = conn.execute(
            "SELECT username, display_name, is_admin FROM users WHERE username=?", (username,)
        ).fetchone()
    if not row:
        return None
    return {
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "groups": groups_for_user(username),
        "has_mfa": has_mfa(username),
    }


def list_users_detailed() -> list[dict]:
    return [get_user(u) for u in list_users()]


def set_display_name(username: str, display_name: str) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE users SET display_name=? WHERE username=?", (display_name, username)
        )
        return cur.rowcount > 0


def set_groups(username: str, groups: list[str]) -> None:
    """Make the user's group membership exactly `groups`."""
    target = {g.strip() for g in groups if g.strip()}
    current = set(groups_for_user(username))
    for g in current - target:
        remove_from_group(username, g)
    for g in target - current:
        add_to_group(username, g)


def set_admin(username: str, is_admin: bool) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE users SET is_admin=? WHERE username=?", (int(is_admin), username)
        )
        return cur.rowcount > 0


def is_admin(username: str) -> bool:
    """Admin if flagged is_admin OR a member of the configured admin group."""
    with _db() as conn:
        row = conn.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            return False
        if row["is_admin"]:
            return True
    return bool(settings.local_admin_group) and settings.local_admin_group in groups_for_user(username)


# ---- groups ----
def groups_for_user(username: str) -> list[str]:
    with _db() as conn:
        return [
            r["group_name"]
            for r in conn.execute(
                "SELECT group_name FROM user_groups WHERE username = ? ORDER BY group_name",
                (username,),
            )
        ]


def add_to_group(username: str, group: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_groups (username, group_name) VALUES (?,?)",
            (username, group),
        )


def remove_from_group(username: str, group: str) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM user_groups WHERE username = ? AND group_name = ?",
            (username, group),
        )
        return cur.rowcount > 0


# ---- MFA: TOTP ----
def set_totp_secret(username: str, secret: str | None) -> None:
    with _db() as conn:
        conn.execute("UPDATE users SET totp_secret=? WHERE username=?", (secret, username))


def get_totp_secret(username: str) -> str | None:
    with _db() as conn:
        row = conn.execute("SELECT totp_secret FROM users WHERE username=?", (username,)).fetchone()
    return row["totp_secret"] if row else None


# ---- MFA: passkeys (WebAuthn) ----
def add_credential(username: str, credential_id: str, public_key: str, sign_count: int, label: str = "") -> None:
    with _db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO webauthn_credentials "
            "(credential_id, username, public_key, sign_count, label) VALUES (?,?,?,?,?)",
            (credential_id, username, public_key, sign_count, label),
        )


def get_credentials(username: str) -> list[dict]:
    with _db() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT credential_id, public_key, sign_count, label "
                "FROM webauthn_credentials WHERE username=?",
                (username,),
            )
        ]


def get_credential(credential_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT credential_id, username, public_key, sign_count "
            "FROM webauthn_credentials WHERE credential_id=?",
            (credential_id,),
        ).fetchone()
    return dict(row) if row else None


def update_sign_count(credential_id: str, sign_count: int) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE webauthn_credentials SET sign_count=? WHERE credential_id=?",
            (sign_count, credential_id),
        )


def has_mfa(username: str) -> bool:
    """True if the user has enrolled at least one MFA factor."""
    return bool(get_totp_secret(username)) or bool(get_credentials(username))


def reset_mfa(username: str) -> None:
    """Remove all enrolled MFA factors (admin recovery). Admin must re-enrol."""
    with _db() as conn:
        conn.execute("UPDATE users SET totp_secret=NULL WHERE username=?", (username,))
        conn.execute("DELETE FROM webauthn_credentials WHERE username=?", (username,))


# ---- auth ----
def authenticate(username: str, password: str) -> dict | None:
    """Verify credentials only. MFA is enforced separately by the login flow."""
    with _db() as conn:
        row = conn.execute(
            "SELECT username, display_name, salt, hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    expected = _hash(password, bytes.fromhex(row["salt"]))
    if not hmac.compare_digest(expected, row["hash"]):
        return None
    return {
        "name": row["display_name"],
        "email": row["username"],
        "oid": row["username"],
        "groups": groups_for_user(username),
        "is_admin": is_admin(username),
    }


def seed_admin() -> None:
    """Create the seed admin if configured and no users exist yet."""
    if not (settings.local_admin_user and settings.local_admin_password):
        return
    if list_users():
        return
    create_user(
        settings.local_admin_user, settings.local_admin_password, "Administrator", is_admin=True
    )
    if settings.local_admin_group:
        add_to_group(settings.local_admin_user, settings.local_admin_group)
