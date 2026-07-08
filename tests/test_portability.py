import json
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from app import business, portability, service
from app.auth import authz, local
from app.config import settings
from app.fpbx import ivr_menus, time_conditions


# ---- pure logic ----
def test_to_review_items():
    doc = {
        "business": {"business_name": "Acme", "templates": {"g": "x"}},
        "schedules": [{"extension": 9550, "label": "Holiday"}],
        "ivrs": [{"extension": 9560, "name": "Main"}],
    }
    items = portability.to_review_items(doc)
    assert [i["type"] for i in items] == ["business", "schedule", "ivr"]
    assert "9550" in items[1]["label"] and "Main" in items[2]["label"]


def test_to_review_items_skips_empty_business():
    assert portability.to_review_items({"business": {"business_name": "", "templates": {}}}) == []


def test_export_document_shape(monkeypatch):
    monkeypatch.setattr(time_conditions, "list_schedules", lambda: [
        {"extension": 9550, "label": "TC-Main", "open_destination": "2000",
         "closures": [
             {"label": "Holiday", "start": datetime(2026, 7, 7, 0, 0),
              "end": datetime(2026, 7, 7, 23, 59), "reason": "a holiday",
              "closed_action": "voicemail"},
         ]}
    ])
    monkeypatch.setattr(ivr_menus, "list_ivrs", lambda: [{"extension": 9560}])
    monkeypatch.setattr(ivr_menus, "get_ivr_full", lambda e: {"extension": e, "name": "Main", "options": []})
    monkeypatch.setattr(business, "load", lambda: {"business_name": "Acme"})
    monkeypatch.setattr(business, "templates", lambda: {"g": "x"})
    doc = portability.export_document()
    assert doc["version"] == portability.EXPORT_VERSION
    assert doc["business"] == {"business_name": "Acme", "templates": {"g": "x"}}
    s = doc["schedules"][0]
    assert s["label"] == "TC-Main" and s["open_destination"] == "2000"
    assert s["closures"][0]["start"] == "2026-07-07T00:00:00"  # datetimes -> ISO
    assert s["closures"][0]["label"] == "Holiday"
    assert doc["ivrs"][0]["extension"] == 9560


def test_commit_item_dispatch(monkeypatch):
    saved = {}
    monkeypatch.setattr(business, "save", lambda n, t: saved.update(name=n, tmpl=t))
    assert portability.commit_item("business", {"business_name": "Acme", "templates": {"g": "x"}})
    assert saved == {"name": "Acme", "tmpl": {"g": "x"}}

    monkeypatch.setattr(service, "apply_time_condition",
                        lambda tc: type("R", (), {"extension": tc.extension, "closure_count": len(tc.closures)})())
    # v1 flat closure still imports (back-compat)
    msg = portability.commit_item("schedule", {
        "label": "L", "start": "2026-07-07T00:00", "end": "2026-07-07T23:59",
        "extension": 9551, "open_destination": "2000"})
    assert "9551" in msg
    # v2 closure list
    msg2 = portability.commit_item("schedule", {
        "label": "TC-Main", "extension": 9552, "open_destination": "2000",
        "closures": [
            {"label": "A", "start": "2026-07-04T00:00", "end": "2026-07-04T23:59"},
            {"label": "B", "start": "2026-09-07T00:00", "end": "2026-09-07T23:59"},
        ]})
    assert "9552" in msg2 and "2 closure" in msg2

    monkeypatch.setattr(service, "create_ivr", lambda req: type("R", (), {"extension": req.extension})())
    msg = portability.commit_item("ivr", {
        "name": "Main", "extension": 9561, "greeting_text": "hi",
        "timeout_destination": "transfer *99100 XML d",
        "options": [{"digits": "0", "destination": "transfer 8000 XML d"}]})
    assert "9561" in msg

    with pytest.raises(ValueError):
        portability.commit_item("bogus", {})


# ---- routes ----
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


def _login(c, u):
    c.cookies.clear()
    c.post("/auth/login", data={"username": u, "password": "pw"}, follow_redirects=False)


def test_import_requires_manage_users(client):
    local.create_user("ed", "pw")
    local.add_to_group("ed", "editors")
    with client as c:
        _login(c, "ed")
        assert c.get("/admin/import", follow_redirects=False).status_code == 403
        assert c.get("/admin/export", follow_redirects=False).status_code == 403


def test_import_review_then_commit(client, monkeypatch):
    committed = []
    monkeypatch.setattr(portability, "commit_item",
                        lambda t, d: committed.append((t, d)) or f"{t} ok")
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    doc = {"business": {"business_name": "Acme", "templates": {"g": "x"}},
           "ivrs": [{"extension": 9560, "name": "Main"}]}
    with client as c:
        _login(c, "ops")
        # upload -> review page lists both items
        r = c.post("/admin/import",
                   files={"file": ("export.json", json.dumps(doc).encode(), "application/json")},
                   follow_redirects=False)
        assert r.status_code == 200
        assert "Review import" in r.text and "Business profile" in r.text and "IVR 9560" in r.text
        # commit only the business item (edited)
        r2 = c.post("/admin/import/commit", data={
            "count": "2",
            "include_0": "on", "type_0": "business", "label_0": "Business profile",
            "data_0": json.dumps({"business_name": "NewCo", "templates": {}}),
            # item 1 (IVR) not included
            "type_1": "ivr", "label_1": "IVR 9560", "data_1": "{}",
        }, follow_redirects=False)
        assert r2.status_code == 200
        assert "Import results" in r2.text and "✓" in r2.text
        assert committed == [("business", {"business_name": "NewCo", "templates": {}})]


def test_import_bad_file(client):
    local.create_user("ops", "pw")
    local.add_to_group("ops", "ops")
    with client as c:
        _login(c, "ops")
        r = c.post("/admin/import", headers={"accept": "text/html"},
                   files={"file": ("x.json", b"not json", "application/json")},
                   follow_redirects=False)
        assert r.status_code == 400
