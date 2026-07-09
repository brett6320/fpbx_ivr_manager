"""Set minimal env so settings-backed modules import during tests."""
import os

_DEFAULTS = {
    "APP_SECRET_KEY": "test-secret",
    "APP_BASE_URL": "https://ivr.test",
    "APP_ORG_NAME": "Acme",
    "ENTRA_TENANT_ID": "t",
    "ENTRA_CLIENT_ID": "c",
    "ENTRA_CLIENT_SECRET": "s",
    "FPBX_DB_HOST": "127.0.0.1",
    "FPBX_DB_USER": "u",
    "FPBX_DB_PASSWORD": "p",
    "FPBX_DOMAIN_NAME": "pbx.test",
    "FS_XMLRPC_URL": "http://127.0.0.1:8787/RPC2",
    "FS_RECORDINGS_DIR": "/var/lib/freeswitch/storage/${FPBX_DOMAIN_NAME}/recordings",
    "GOOGLE_TTS_CREDENTIALS_FILE": "",
    # tests have no live FusionPBX DB; skip the startup schema check so app
    # startup (TestClient) doesn't block on connection attempts
    "FPBX_SCHEMA_CHECK": "off",
}
for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)

import pytest


@pytest.fixture(autouse=True)
def _stub_db_destinations(monkeypatch):
    """Form renders now list DB destinations; there's no live DB in tests, so
    default it to empty. Tests that assert specific destinations override this."""
    from app.web import routes
    monkeypatch.setattr(routes, "list_destinations", lambda: [], raising=False)
    yield
