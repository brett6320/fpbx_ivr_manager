import sqlite3

import pytest
from starlette.testclient import TestClient

from app import audit
from app.auth import authz, local
from app.config import settings


# ---- audit store: append + hash chain + tamper detection ----
@pytest.fixture
def audit_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audit_db", str(tmp_path / "audit.db"))
    return str(tmp_path / "audit.db")


def test_record_and_entries_newest_first(audit_db):
    audit.record("alice", "user.create", target="bob", details={"is_admin": True})
    audit.record("alice", "user.delete", target="bob")
    rows = audit.entries()
    assert [r["action"] for r in rows] == ["user.delete", "user.create"]
    assert rows[1]["actor"] == "alice" and rows[1]["target"] == "bob"
    assert audit.count() == 2


def test_first_entry_is_genesis_and_chain_links(audit_db):
    audit.record("a", "one")
    audit.record("a", "two")
    asc = list(reversed(audit.entries()))  # oldest -> newest
    assert asc[0]["prev_hash"] == audit.GENESIS
    assert asc[1]["prev_hash"] == asc[0]["hash"]  # each links to the previous hash


def test_verify_ok_on_untouched_log(audit_db):
    for i in range(5):
        audit.record("a", f"act{i}")
    assert audit.verify() == {"ok": True, "count": 5, "bad_seq": None}


def test_verify_detects_modified_entry(audit_db):
    audit.record("a", "one")
    audit.record("a", "two", target="x")
    audit.record("a", "three")
    with sqlite3.connect(audit_db) as conn:
        conn.execute("UPDATE audit SET target='HACKED' WHERE seq=2")
        conn.commit()
    v = audit.verify()
    assert v["ok"] is False and v["bad_seq"] == 2


def test_verify_detects_removed_entry(audit_db):
    for i in range(4):
        audit.record("a", f"act{i}")
    with sqlite3.connect(audit_db) as conn:
        conn.execute("DELETE FROM audit WHERE seq=2")
        conn.commit()
    assert audit.verify()["ok"] is False


def test_record_never_raises(monkeypatch):
    # unwritable location: the audit write must fail silently, not break callers
    monkeypatch.setattr(settings, "audit_db", "/proc/nonexistent/audit.db")
    audit.record("a", "boom")  # must not raise


# ---- route: admin-only visibility + action recording ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "audit_db", str(tmp_path / "audit.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"ops":["manage_users","manage_schedules","view_audit"],'
        '"editors":["manage_schedules"]}',
    )
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def _login(c, u):
    c.cookies.clear()
    c.post("/auth/login", data={"username": u, "password": "pw"}, follow_redirects=False)


def test_audit_view_is_admin_only(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        # editor lacks view_audit -> 403
        assert c.get("/admin/audit", follow_redirects=False).status_code == 403


def test_audit_view_visible_to_admin_and_records_actions(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        assert "Audit log" in c.get("/admin/audit").text
        # a mutating action gets recorded
        c.post("/admin/users", data={"username": "carol", "password": "pw",
                                     "display_name": "Carol"}, follow_redirects=False)
        page = c.get("/admin/audit").text
        assert "user.create" in page and "carol" in page
        assert "Chain verified" in page  # integrity banner
