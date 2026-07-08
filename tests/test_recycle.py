import pytest
from starlette.testclient import TestClient

from app import recycle, service
from app.auth import authz, local
from app.config import settings
from app.web import routes


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "recycle_log_file", str(tmp_path / "recycled.json"))


def test_record_and_entries_roundtrip(ledger):
    assert recycle.entries() == []
    recycle.record(9560, kind="ivr", name="Main Menu", actor="admin@x")
    recycle.record(9561, kind="ivr", name="Sales", actor="admin@x")
    ents = recycle.entries()
    assert [e["extension"] for e in ents] == [9561, 9560]  # newest first
    assert ents[1]["previous_name"] == "Main Menu"
    assert ents[0]["recycled_by"] == "admin@x"
    assert ents[0]["kind"] == "ivr"
    assert ents[0]["recycled_at"]  # timestamp present


def test_entries_tolerates_missing_or_bad_file(ledger, tmp_path):
    assert recycle.entries() == []
    (tmp_path / "recycled.json").write_text("{ not json")
    assert recycle.entries() == []


def test_recycle_ivr_logs_then_frees(ledger, monkeypatch):
    freed = []
    monkeypatch.setattr(service.ivr_menus, "get_ivr", lambda e: {"extension": e, "name": "Main Menu"})
    monkeypatch.setattr(service.ivr_menus, "delete_ivr", lambda e: freed.append(e) or True)
    monkeypatch.setattr(service.xmlrpc_client, "reloadxml", lambda: True)

    name = service.recycle_ivr(9560, actor="ops@x")
    assert name == "Main Menu"
    assert freed == [9560]                       # dialplan freed
    logged = recycle.entries()
    assert logged and logged[0]["extension"] == 9560 and logged[0]["previous_name"] == "Main Menu"


def test_recycle_ivr_noop_when_absent(ledger, monkeypatch):
    monkeypatch.setattr(service.ivr_menus, "get_ivr", lambda e: None)
    assert service.recycle_ivr(9599, actor="ops@x") is None
    assert recycle.entries() == []              # nothing logged


def test_list_recycled_flags_availability(ledger, monkeypatch):
    recycle.record(9560, kind="ivr", name="Old", actor="x")
    recycle.record(9561, kind="ivr", name="Reused", actor="x")
    # 9560 free, 9561 back in use
    monkeypatch.setattr(service.extensions, "used_extensions", lambda: {9561})
    by_ext = {r["extension"]: r["available"] for r in service.list_recycled()}
    assert by_ext == {9560: True, 9561: False}


# ---- route: recycle needs confirmation and logs the acting user ----
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_auth_db", str(tmp_path / "users.db"))
    monkeypatch.setattr(settings, "auth_backend", "local")
    monkeypatch.setattr(settings, "local_admin_group", "local-admins")
    monkeypatch.setattr(
        settings, "authz_group_permissions",
        '{"ops":["manage_users","manage_schedules"],"editors":["manage_schedules"]}')
    authz._group_map.cache_clear()
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_recycle_route_requires_confirm_and_passes_actor(client, monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "recycle_ivr", lambda ext, *, actor: calls.append((ext, actor)))
    with client as c:
        local.create_user("ops", "pw")
        local.add_to_group("ops", "ops")
        c.post("/auth/login", data={"username": "ops", "password": "pw"}, follow_redirects=False)
        assert c.post("/ivrs/9560/recycle", follow_redirects=False).status_code == 400
        assert calls == []
        r = c.post("/ivrs/9560/recycle", data={"confirm": "yes"}, follow_redirects=False)
        assert r.status_code == 303
        assert calls == [(9560, "ops")]  # actor logged (local username as email)


def test_remove_ledger_entry(ledger):
    recycle.record(9560, kind="ivr", name="A", actor="x")
    recycle.record(9561, kind="ivr", name="B", actor="x")
    at = [e for e in recycle.entries() if e["extension"] == 9560][0]["recycled_at"]
    assert recycle.remove(9560, at) is True
    assert [e["extension"] for e in recycle.entries()] == [9561]
    assert recycle.remove(9560, at) is False   # already gone


def test_recycled_delete_route_is_admin_only_and_confirmed(client, monkeypatch):
    removed = []
    monkeypatch.setattr(routes, "delete_recycled",
                        lambda ext, at: removed.append((ext, at)) or True)
    monkeypatch.setattr(routes, "list_ivrs", lambda: [])
    monkeypatch.setattr(routes, "list_recycled",
                        lambda: [{"extension": 9560, "previous_name": "A",
                                  "recycled_at": "2026-07-08T00:00:00+00:00",
                                  "recycled_by": "x", "available": True}])
    with client as c:
        # non-admin editor: no control, endpoint forbidden
        local.create_user("ed", "pw")
        local.add_to_group("ed", "editors")
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        assert "/ivrs/recycled/delete" not in c.get("/ivrs").text
        assert c.post("/ivrs/recycled/delete",
                      data={"confirm": "yes", "extension": "9560", "recycled_at": "x"},
                      follow_redirects=False).status_code == 403
        # admin: confirm required, then deletes
        c.cookies.clear()
        local.create_user("ops", "pw")
        local.add_to_group("ops", "ops")
        c.post("/auth/login", data={"username": "ops", "password": "pw"}, follow_redirects=False)
        assert c.post("/ivrs/recycled/delete",
                      data={"extension": "9560", "recycled_at": "t"},
                      follow_redirects=False).status_code == 400   # no confirm
        assert removed == []
        r = c.post("/ivrs/recycled/delete",
                   data={"confirm": "yes", "extension": "9560", "recycled_at": "t"},
                   follow_redirects=False)
        assert r.status_code == 303
        assert removed == [(9560, "t")]


def test_ivr_delete_and_recycle_are_admin_only(client, monkeypatch):
    # a schedules-only user can view IVRs but cannot delete or recycle them
    monkeypatch.setattr(routes, "list_ivrs", lambda: [{"extension": 9560, "name": "Main", "enabled": True}])
    monkeypatch.setattr(routes, "list_recycled", lambda: [])
    with client as c:
        local.create_user("ed", "pw")
        local.add_to_group("ed", "editors")
        c.post("/auth/login", data={"username": "ed", "password": "pw"}, follow_redirects=False)
        page = c.get("/ivrs", follow_redirects=False)
        assert page.status_code == 200
        assert "9560" in page.text                    # can view
        assert "/ivrs/9560/delete" not in page.text   # no admin controls
        assert "/ivrs/9560/recycle" not in page.text
        for path in ("/ivrs/9560/delete", "/ivrs/9560/recycle"):
            assert c.post(path, data={"confirm": "yes"}, follow_redirects=False).status_code == 403
