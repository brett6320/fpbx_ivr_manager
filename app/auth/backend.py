"""Auth backend dispatcher.

Selected by settings.auth_backend: 'local' (default) | 'entra' | 'ldap' | 'fpbx'.

  - local / ldap / fpbx  -> password login (form-based, `password_login`)
  - entra                -> SSO redirect flow (see app/auth/entra.py)
"""
from __future__ import annotations

from app.config import settings

VALID = {"local", "entra", "ldap", "fpbx"}


def kind() -> str:
    b = settings.auth_backend.lower()
    if b not in VALID:
        raise ValueError(f"AUTH_BACKEND={b!r} invalid; choose one of {sorted(VALID)}")
    return b


def is_sso() -> bool:
    return kind() == "entra"


def validate_config() -> None:
    """Fail fast at startup if the selected backend is misconfigured."""
    b = kind()
    if b == "entra":
        missing = [
            k for k in ("entra_tenant_id", "entra_client_id", "entra_client_secret")
            if not getattr(settings, k)
        ]
        if missing:
            raise RuntimeError(f"AUTH_BACKEND=entra requires: {', '.join(missing)}")
    elif b == "ldap":
        if not (settings.ldap_uri and settings.ldap_bind_dn_template):
            raise RuntimeError("AUTH_BACKEND=ldap requires LDAP_URI and LDAP_BIND_DN_TEMPLATE")


def password_login(username: str, password: str) -> dict | None:
    """Validate credentials for the password backends. Returns a user dict or None."""
    b = kind()
    if b == "local":
        from app.auth import local
        return local.authenticate(username, password)
    if b == "ldap":
        from app.auth import ldap_backend
        return ldap_backend.authenticate(username, password)
    if b == "fpbx":
        from app.auth import fpbx_backend
        return fpbx_backend.authenticate(username, password)
    raise RuntimeError(f"password_login not supported for backend {b!r}")
