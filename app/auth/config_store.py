"""Read/write the app-managed auth override file (dotenv format).

Only a whitelist of auth-related keys may be written, so the admin UI cannot
inject arbitrary settings. The file lives in the app's writable state dir
(app.config.AUTH_CONFIG_FILE) and is created 0600.
"""
from __future__ import annotations

import os

from app.config import AUTH_CONFIG_FILE

# Keys the admin UI is allowed to manage.
MANAGED_KEYS = {
    "AUTH_BACKEND",
    "AUTHZ_GROUP_PERMISSIONS",
    "ENTRA_TENANT_ID",
    "ENTRA_CLIENT_ID",
    "ENTRA_CLIENT_SECRET",
    "LDAP_URI",
    "LDAP_BIND_DN_TEMPLATE",
    "LDAP_BASE_DN",
    "LDAP_USER_FILTER",
    "LDAP_GROUP_BASE_DN",
    "LDAP_GROUP_FILTER",
    "LDAP_START_TLS",
}

SECRET_KEYS = {"ENTRA_CLIENT_SECRET"}


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_managed(path: str = AUTH_CONFIG_FILE) -> dict[str, str]:
    try:
        with open(path) as f:
            return _parse(f.read())
    except FileNotFoundError:
        return {}


def write_managed(updates: dict[str, str], path: str = AUTH_CONFIG_FILE) -> list[str]:
    """Upsert whitelisted keys into the managed file. Returns the keys written."""
    rejected = set(updates) - MANAGED_KEYS
    if rejected:
        raise ValueError(f"refusing to write non-managed keys: {sorted(rejected)}")

    current = read_managed(path)
    # empty string means "unset" -> drop the key
    for k, v in updates.items():
        if v == "":
            current.pop(k, None)
        else:
            current[k] = v

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lines = [
        "# Managed by the FusionPBX IVR Manager admin UI. Restart to apply.",
        "# Do not also set these as process env vars (those take precedence).",
    ]
    lines += [f"{k}={current[k]}" for k in sorted(current)]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return sorted(updates)
