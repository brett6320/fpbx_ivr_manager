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


def test_hash_status_flags_the_tampered_entry(audit_db):
    for i in range(4):
        audit.record("a", f"act{i}")
    st = audit.hash_status()
    assert len(st) == 4 and all(st.values())  # clean chain: every entry valid
    with sqlite3.connect(audit_db) as conn:
        conn.execute("UPDATE audit SET actor='HACKED' WHERE seq=2")
        conn.commit()
    st2 = audit.hash_status()
    assert st2[2] is False and st2[1] is True  # only the tampered entry is flagged


def test_verify_detects_removed_entry(audit_db):
    for i in range(4):
        audit.record("a", f"act{i}")
    with sqlite3.connect(audit_db) as conn:
        conn.execute("DELETE FROM audit WHERE seq=2")
        conn.commit()
    assert audit.verify()["ok"] is False


def test_search_action_filter_and_pagination(audit_db):
    audit.record("alice", "user.create", target="bob", ip="10.0.0.1")
    audit.record("carol", "user.delete", target="bob")
    audit.record("alice", "login.success", ua="Firefox/1")
    # free-text search spans actor/action/target/details/ip/ua
    assert {r["action"] for r in audit.entries(search="alice")} == {"user.create", "login.success"}
    assert {r["action"] for r in audit.entries(search="Firefox")} == {"login.success"}
    assert len(audit.entries(search="bob")) == 2
    assert len(audit.entries(search="10.0.0.1")) == 1
    # exact action filter + filtered counts
    assert [r["actor"] for r in audit.entries(action="user.delete")] == ["carol"]
    assert audit.count(search="alice") == 2
    assert audit.count(action="user.create") == 1
    assert set(audit.distinct_actions()) == {"user.create", "user.delete", "login.success"}


def test_pagination_slices_without_overlap(audit_db):
    for i in range(10):
        audit.record("a", f"act{i}")
    p1 = audit.entries(limit=4, offset=0)
    p2 = audit.entries(limit=4, offset=4)
    assert len(p1) == 4 and len(p2) == 4
    assert {r["seq"] for r in p1}.isdisjoint({r["seq"] for r in p2})


def test_audit_page_search_and_empty_filter(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        c.post("/admin/users", data={"username": "carol", "password": "pw",
                                     "display_name": "Carol"}, follow_redirects=False)
        assert "user.create" in c.get("/admin/audit?q=carol").text
        assert "No entries match" in c.get("/admin/audit?action=nope.nope").text


def test_record_never_raises(monkeypatch):
    # unwritable location: the audit write must fail silently, not break callers
    monkeypatch.setattr(settings, "audit_db", "/proc/nonexistent/audit.db")
    audit.record("a", "boom")  # must not raise


def test_concurrent_records_keep_the_chain_intact(audit_db):
    # contention: many threads appending at once must not fork the hash chain
    import threading

    def worker():
        for i in range(25):
            audit.record("t", "concurrent", details={"i": i})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert audit.count() == 8 * 25
    assert audit.verify()["ok"] is True  # chain still valid despite contention


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


def test_activity_middleware_logs_page_views(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        c.get("/account", follow_redirects=False)  # a read/navigation
        page = c.get("/admin/audit").text
        # the middleware recorded the page view; /admin/audit itself is excluded
        assert "view" in page and "/account" in page


def test_login_success_and_failure_are_recorded(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        c.post("/auth/login", data={"username": "ops", "password": "wrong"},
               follow_redirects=False)
        _login(c, "ops")
        page = c.get("/admin/audit").text
        assert "login.failure" in page and "login.success" in page


# ---- source IP (proxy-aware) ----
def _scope(headers: dict, client=("10.0.0.1", 5555)):
    return {"type": "http",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "client": client}


def test_client_ip_resolves_through_proxy_layers():
    from app.audit_mw import client_ip_from_scope
    # Cloudflare header wins over everything
    assert client_ip_from_scope(_scope(
        {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "1.1.1.1"})) == "203.0.113.9"
    # left-most X-Forwarded-For is the original client
    assert client_ip_from_scope(_scope(
        {"x-forwarded-for": "203.0.113.7, 10.0.0.2, 10.0.0.3"})) == "203.0.113.7"
    # X-Real-IP next
    assert client_ip_from_scope(_scope({"x-real-ip": "203.0.113.5"})) == "203.0.113.5"
    # fall back to the direct peer when no proxy headers
    assert client_ip_from_scope(_scope({})) == "10.0.0.1"


def test_record_stamps_context_ip_and_mixed_chain_verifies(audit_db):
    audit.set_client_ip("203.0.113.7")
    try:
        audit.record("a", "with_ip")
    finally:
        audit.set_client_ip(None)
    audit.record("a", "without_ip")  # no ip in context
    ips = {r["action"]: r["ip"] for r in audit.entries()}
    assert ips["with_ip"] == "203.0.113.7" and ips["without_ip"] is None
    # a chain mixing ip and NULL-ip entries still verifies (NULL-ip entries hash
    # exactly as pre-ip-column entries did)
    assert audit.verify()["ok"] is True


def test_forwarded_ip_flows_through_middleware_to_the_log(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        c.get("/account", headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.9"},
              follow_redirects=False)
        assert "203.0.113.7" in c.get("/admin/audit").text


def test_user_agent_from_scope():
    from app.audit_mw import user_agent_from_scope
    assert user_agent_from_scope(_scope({"user-agent": "Foo/1.0"})) == "Foo/1.0"
    assert user_agent_from_scope(_scope({})) is None


def test_record_stamps_context_ua_and_chain_verifies(audit_db):
    audit.set_user_agent("Mozilla/5.0 Test")
    try:
        audit.record("a", "with_ua")
    finally:
        audit.set_user_agent(None)
    assert audit.entries()[0]["ua"] == "Mozilla/5.0 Test"
    assert audit.verify()["ok"] is True


def test_user_agent_flows_through_middleware_to_the_log(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        c.get("/account", headers={"User-Agent": "MyTestAgent/9.9"},
              follow_redirects=False)
        assert "MyTestAgent/9.9" in c.get("/admin/audit").text
