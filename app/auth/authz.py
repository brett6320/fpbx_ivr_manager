"""Group-based authorization.

Permissions are bound to GROUPS, never to individual users. A user's effective
permissions are the union of the permissions of every group they belong to.
Group membership is supplied by the auth backend at login:
  - local : internal groups (SQLite user_groups table)
  - entra : the token 'groups' claim
  - ldap  : the user's directory group memberships

The group -> permission map lives in settings.authz_group_permissions (JSON).
There is intentionally no user -> permission map anywhere in the code.
"""
from __future__ import annotations

import json
from functools import lru_cache

from fastapi import Request
from starlette.exceptions import HTTPException

from app.config import settings

# Known permissions
MANAGE_SCHEDULES = "manage_schedules"
MANAGE_USERS = "manage_users"
ALL_PERMISSIONS = {MANAGE_SCHEDULES, MANAGE_USERS}

# Default group->permission map used when AUTH_BACKEND=fpbx and no explicit
# AUTHZ_GROUP_PERMISSIONS is configured — keyed by FusionPBX's common group names.
FPBX_DEFAULT_GROUP_PERMISSIONS: dict[str, list[str]] = {
    "ivr-admins": [MANAGE_SCHEDULES, MANAGE_USERS],
    "ivr-editors": [MANAGE_SCHEDULES],
    "admin": [MANAGE_SCHEDULES],
    "superadmin": [MANAGE_SCHEDULES, MANAGE_USERS],
    "user": [MANAGE_SCHEDULES],
}


def _raw_group_permissions() -> dict:
    """The configured mapping, or the fpbx-backend default when unset."""
    raw = json.loads(settings.authz_group_permissions or "{}")
    if not raw and settings.auth_backend.lower() == "fpbx":
        return dict(FPBX_DEFAULT_GROUP_PERMISSIONS)
    return raw


def group_permissions_json() -> str:
    """Effective mapping as JSON (what the admin page shows / _group_map uses)."""
    return json.dumps(_raw_group_permissions())


@lru_cache(maxsize=1)
def _group_map() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for group, perms in _raw_group_permissions().items():
        out[str(group)] = {str(p) for p in perms}
    return out


def permissions_for(groups: list[str] | None) -> set[str]:
    """Union of permissions across the user's groups. No group -> no permission."""
    gm = _group_map()
    perms: set[str] = set()
    for g in groups or []:
        perms |= gm.get(str(g), set())
    return perms


def user_permissions(user: dict) -> set[str]:
    # A local administrator is the app's superuser role and holds every
    # permission. (is_admin is only ever set by the local backend — IdP users
    # still derive permissions purely from their groups.)
    if user.get("is_admin"):
        return set(ALL_PERMISSIONS)
    return permissions_for(user.get("groups"))


def has_permission(user: dict, permission: str) -> bool:
    return permission in user_permissions(user)


def require(permission: str):
    """FastAPI dependency factory: 401 if not logged in, 403 if lacking permission."""

    def _dep(request: Request) -> dict:
        user = request.session.get("user")
        if not user:
            raise HTTPException(status_code=307, headers={"Location": "/auth/login"})
        if not has_permission(user, permission):
            raise HTTPException(status_code=403, detail=f"requires permission: {permission}")
        return user

    return _dep
