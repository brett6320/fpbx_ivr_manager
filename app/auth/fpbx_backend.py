"""Authenticate against the FusionPBX user database (v_users).

Selected with AUTH_BACKEND=fpbx. The app already has a PostgreSQL connection to
FusionPBX, so this reuses it: it looks up the user in the configured domain,
verifies the password against FusionPBX's own hashing schemes, and returns the
user's FusionPBX group memberships as `groups` — which map to app permissions
through AUTHZ_GROUP_PERMISSIONS exactly like the Entra/LDAP backends.

Password schemes (matching FusionPBX's own auth, across 4.5.x–5.5):
  - modern: bcrypt / crypt hash, stored `$2y$…` (also accepts `$2a$`/`$2b$`)
  - legacy: md5(salt + password), or md5(password)

Permissions are still bound to groups only; this backend never sets is_admin
(that superuser flag is exclusive to the local backend).
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from app.config import settings
from app.fpbx.db import cursor, domain_uuid

log = logging.getLogger("fpbx_ivr_manager")


def _verify_password(password: str, stored: str | None, salt: str | None) -> bool:
    """Constant-time-ish verification against FusionPBX's stored hash."""
    stored = (stored or "").strip()
    if not stored:
        return False
    if stored.startswith("$"):
        if stored.startswith(("$2a$", "$2b$", "$2y$")):
            try:
                import bcrypt
            except ImportError as e:  # pragma: no cover - depends on install extras
                raise RuntimeError(
                    "FusionPBX user has a bcrypt password but the 'bcrypt' package "
                    "is not installed; run: pip install '.[fpbx]'"
                ) from e
            h = stored.encode()
            if h.startswith(b"$2y$"):  # PHP uses $2y$; python-bcrypt wants $2a/$2b
                h = b"$2b$" + h[4:]
            try:
                return bcrypt.checkpw(password.encode(), h)
            except ValueError:
                return False
        # some other crypt scheme we don't implement ($6$ sha512, argon2, …)
        log.warning("fpbx auth: unsupported password hash scheme")
        return False
    # legacy md5 fallbacks (FusionPBX's own legacy behavior)
    salt = salt or ""
    candidates = [
        hashlib.md5((salt + password).encode()).hexdigest(),  # noqa: S324 - legacy FPBX scheme
        hashlib.md5(password.encode()).hexdigest(),           # noqa: S324 - legacy FPBX scheme
    ]
    return any(hmac.compare_digest(stored.lower(), c) for c in candidates)


def _find_user(cur, username: str, d: str) -> dict | None:
    """The enabled user for this username, preferring the configured domain and
    falling back to a global (domain-less) FusionPBX superadmin account.

    Note: email is intentionally NOT read here — FusionPBX keeps it in v_contacts
    (via contact_uuid), not v_users, and some schema versions have no email column
    on v_users at all. The session email falls back to the username."""
    cur.execute(
        "SELECT user_uuid, username, password, salt, user_enabled, domain_uuid "
        "FROM v_users WHERE username = %s AND (domain_uuid = %s OR domain_uuid IS NULL)",
        (username, d),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    rows.sort(key=lambda r: 0 if r["domain_uuid"] == d else 1)  # domain match first
    for r in rows:
        # user_enabled may be a real boolean (this schema) or a "true"/"false"
        # string (older FusionPBX). Treat NULL as enabled (FusionPBX default).
        ue = r["user_enabled"]
        if ue is None or ue is True or (
            isinstance(ue, str) and ue.strip().lower() in ("true", "t", "1", "yes", "y")
        ):
            return r
    return None


def _user_groups(cur, user_uuid: str, d: str) -> list[str]:
    # This FusionPBX schema maps users to groups in v_user_groups. Join
    # v_groups on group_uuid so we don't depend on the denormalized group_name
    # column that some schema versions omit from the membership table.
    cur.execute(
        "SELECT g.group_name FROM v_user_groups gu "
        "JOIN v_groups g ON g.group_uuid = gu.group_uuid "
        "WHERE gu.user_uuid = %s AND (gu.domain_uuid = %s OR gu.domain_uuid IS NULL)",
        (user_uuid, d),
    )
    return [r["group_name"] for r in cur.fetchall() if r["group_name"]]


def authenticate(username: str, password: str) -> dict | None:
    """Verify credentials against v_users. Returns the session user dict or None.
    MFA is enforced separately by the login flow."""
    if not username or not password:
        return None
    d = domain_uuid()
    with cursor() as cur:
        row = _find_user(cur, username, d)
        if not row or not _verify_password(password, row["password"], row.get("salt")):
            return None
        groups = _user_groups(cur, row["user_uuid"], d)
    return {
        "name": row["username"],
        "email": row["username"],   # FusionPBX email lives in v_contacts, not read here
        # psycopg returns uuid columns as uuid.UUID; str() so the session (a
        # signed cookie serialized with json.dumps) can encode it
        "oid": str(row["user_uuid"]),
        "groups": groups,
    }


def list_groups() -> list[str]:
    """Distinct FusionPBX group names in the configured domain (plus global
    groups), for the admin group→permission mapping UI."""
    d = domain_uuid()
    with cursor() as cur:
        cur.execute(
            "SELECT DISTINCT group_name FROM v_groups "
            "WHERE (domain_uuid = %s OR domain_uuid IS NULL) AND group_name IS NOT NULL "
            "ORDER BY group_name",
            (d,),
        )
        return [r["group_name"] for r in cur.fetchall()]


def probe(username: str, password: str) -> dict:
    """Live test for the admin auth page: verify a credential and show which
    permissions the resolved groups grant. Never returns the stored hash."""
    from app.auth import authz

    steps: list[dict] = []
    try:
        domain_uuid()  # verify the configured domain resolves
        steps.append({"name": "resolve domain", "ok": True, "detail": settings.fpbx_domain_name})
    except Exception:
        return {"ok": False, "steps": [{"name": "resolve domain", "ok": False,
                                        "detail": "could not resolve the configured FusionPBX domain"}]}
    user = authenticate(username, password)
    if not user:
        steps.append({"name": "verify credentials", "ok": False,
                      "detail": "no enabled user matched, or the password was wrong"})
        return {"ok": False, "steps": steps}
    steps.append({"name": "verify credentials", "ok": True, "detail": f"user {user['name']}"})
    steps.append({"name": "groups", "ok": True,
                  "detail": ", ".join(user["groups"]) or "(none)"})
    perms = sorted(authz.permissions_for(user["groups"]))
    steps.append({"name": "mapped permissions", "ok": bool(perms),
                  "detail": ", ".join(perms) or "(none — grant via AUTHZ_GROUP_PERMISSIONS)"})
    return {"ok": True, "steps": steps}
