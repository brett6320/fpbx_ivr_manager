"""Entra ID (Azure AD) SSO via MSAL authorization-code flow."""
from __future__ import annotations

import msal

from app.config import settings

SCOPES: list[str] = []  # OIDC 'openid profile' added implicitly by MSAL


def _app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.entra_client_id,
        client_credential=settings.entra_client_secret,
        authority=settings.entra_authority,
    )


def auth_url(state: str) -> str:
    return _app().get_authorization_request_url(
        SCOPES,
        state=state,
        redirect_uri=settings.redirect_uri,
    )


def redeem_code(code: str) -> dict:
    """Exchange the auth code for tokens; returns MSAL result (contains id_token_claims)."""
    result = _app().acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=settings.redirect_uri,
    )
    if "error" in result:
        raise PermissionError(result.get("error_description", result["error"]))
    return result


def user_from_claims(claims: dict) -> dict:
    """Extract the identity + group memberships we keep in session.

    Authorization is decided from group membership (see app.auth.authz), so we
    carry the token 'groups' claim through. Ensure the app registration emits a
    groups claim (Token configuration -> add groups claim), or use group names
    via an optional-claims transformation. Values here are matched against the
    keys of AUTHZ_GROUP_PERMISSIONS.
    """
    groups = claims.get("groups", [])
    if isinstance(groups, str):
        groups = [groups]
    return {
        "name": claims.get("name"),
        "email": claims.get("preferred_username") or claims.get("email"),
        "oid": claims.get("oid"),
        "groups": list(groups),
    }
