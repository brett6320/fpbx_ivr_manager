import hashlib

import pytest

from app.auth import backend, fpbx_backend


# ---- password verification across FusionPBX's schemes ----
def test_verify_legacy_md5_salted():
    stored = hashlib.md5(b"s4ltpassword").hexdigest()
    assert fpbx_backend._verify_password("password", stored, "s4lt") is True
    assert fpbx_backend._verify_password("wrong", stored, "s4lt") is False


def test_verify_legacy_md5_unsalted():
    stored = hashlib.md5(b"password").hexdigest()
    assert fpbx_backend._verify_password("password", stored, "") is True
    assert fpbx_backend._verify_password("password", stored, None) is True


def test_verify_empty_or_unsupported():
    assert fpbx_backend._verify_password("x", "", "") is False
    assert fpbx_backend._verify_password("x", None, "") is False
    assert fpbx_backend._verify_password("x", "$6$rounds=...$abc", "") is False  # sha512-crypt: unsupported


def test_verify_bcrypt_including_php_2y_prefix():
    bcrypt = pytest.importorskip("bcrypt")
    h = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode()  # $2b$...
    assert fpbx_backend._verify_password("secret", h, "") is True
    assert fpbx_backend._verify_password("nope", h, "") is False
    # PHP stores $2y$ — the backend rewrites the prefix so it still verifies
    php = "$2y$" + h[4:]
    assert fpbx_backend._verify_password("secret", php, "") is True


# ---- authenticate() end-to-end with the DB layer stubbed ----
def test_authenticate_success_maps_groups(monkeypatch):
    stored = hashlib.md5(b"pw").hexdigest()
    monkeypatch.setattr(fpbx_backend, "domain_uuid", lambda: "dom-1")
    monkeypatch.setattr(fpbx_backend, "_find_user", lambda cur, u, d: {
        "user_uuid": "u-1", "username": u, "password": stored, "salt": "",
        "user_enabled": "true", "domain_uuid": d})
    monkeypatch.setattr(fpbx_backend, "_user_groups", lambda cur, uid, d: ["ivr-admins"])
    monkeypatch.setattr(fpbx_backend, "cursor", _fake_cursor)

    user = fpbx_backend.authenticate("alice", "pw")
    # email falls back to the FusionPBX username (v_users has no email column)
    assert user == {"name": "alice", "email": "alice", "oid": "u-1", "groups": ["ivr-admins"]}


def test_authenticate_wrong_password_returns_none(monkeypatch):
    stored = hashlib.md5(b"pw").hexdigest()
    monkeypatch.setattr(fpbx_backend, "domain_uuid", lambda: "dom-1")
    monkeypatch.setattr(fpbx_backend, "_find_user", lambda cur, u, d: {
        "user_uuid": "u-1", "username": u, "password": stored, "salt": "", "domain_uuid": d})
    monkeypatch.setattr(fpbx_backend, "cursor", _fake_cursor)
    assert fpbx_backend.authenticate("alice", "nope") is None


def test_authenticate_no_user_returns_none(monkeypatch):
    monkeypatch.setattr(fpbx_backend, "domain_uuid", lambda: "dom-1")
    monkeypatch.setattr(fpbx_backend, "_find_user", lambda cur, u, d: None)
    monkeypatch.setattr(fpbx_backend, "cursor", _fake_cursor)
    assert fpbx_backend.authenticate("ghost", "pw") is None
    assert fpbx_backend.authenticate("", "pw") is None  # empty short-circuits


# ---- _find_user prefers domain match + requires enabled ----
def test_find_user_prefers_domain_and_requires_enabled():
    rows = [
        {"user_uuid": "g", "username": "a", "password": "x", "salt": "",
         "user_enabled": "true", "domain_uuid": None},          # global
        {"user_uuid": "d", "username": "a", "password": "x", "salt": "",
         "user_enabled": "true", "domain_uuid": "dom-1"},        # domain match
    ]
    cur = _Cur(rows)
    assert fpbx_backend._find_user(cur, "a", "dom-1")["user_uuid"] == "d"

    disabled = _Cur([{"user_uuid": "d", "username": "a", "password": "x", "salt": "",
                      "user_enabled": "false", "domain_uuid": "dom-1"}])
    assert fpbx_backend._find_user(disabled, "a", "dom-1") is None


def test_find_user_query_does_not_reference_user_email():
    # regression: v_users has no email column on many FusionPBX schemas
    cur = _Cur([])
    fpbx_backend._find_user(cur, "a", "dom-1")
    assert "user_email" not in cur.sql


def test_user_groups_uses_v_group_users_table():
    # regression: the mapping table is v_group_users, not v_user_groups
    cur = _Cur([{"group_name": "ivr-admins"}])
    groups = fpbx_backend._user_groups(cur, "u-1", "dom-1")
    assert groups == ["ivr-admins"]
    assert "v_group_users" in cur.sql and "v_user_groups" not in cur.sql


# ---- dispatcher wiring ----
def test_backend_dispatch_fpbx(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "auth_backend", "fpbx")
    assert backend.kind() == "fpbx"
    assert backend.is_sso() is False
    called = {}
    monkeypatch.setattr(fpbx_backend, "authenticate",
                        lambda u, p: called.update(args=(u, p)) or {"name": u})
    assert backend.password_login("bob", "pw") == {"name": "bob"}
    assert called["args"] == ("bob", "pw")


# --- helpers ---
class _Cur:
    def __init__(self, rows):
        self._rows = rows
        self.sql = ""

    def execute(self, sql, *a, **k):
        self.sql = sql

    def fetchall(self):
        return list(self._rows)


class _FakeCM:
    def __enter__(self):
        return _Cur([])

    def __exit__(self, *a):
        return False


def _fake_cursor():
    return _FakeCM()
