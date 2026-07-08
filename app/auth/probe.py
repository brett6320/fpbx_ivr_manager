"""Live configuration tests for the interactive SSO/LDAP enablement UI.

Each probe takes explicit config (from the admin form, NOT global settings) and
returns a structured result: an overall ok flag, ordered step results, and —
where applicable — resolved groups and which permissions they map to. Secrets are
never echoed back.
"""
from __future__ import annotations

import json

import httpx


def _step(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _mapped(groups: list[str], group_permissions: str | dict) -> dict[str, list[str]]:
    gm = group_permissions if isinstance(group_permissions, dict) else json.loads(group_permissions or "{}")
    return {g: sorted(gm.get(g, [])) for g in groups if g in gm}


# ---- LDAP ----
def ldap_probe(
    *,
    uri: str,
    bind_dn_template: str,
    base_dn: str,
    user_filter: str,
    group_base_dn: str,
    group_filter: str,
    start_tls: bool,
    test_user: str,
    test_password: str,
    group_permissions: str | dict = "{}",
) -> dict:
    steps: list[dict] = []
    groups: list[str] = []
    try:
        import ldap3
    except ImportError:
        return {"ok": False, "steps": [_step("import ldap3", False, "pip install '.[ldap]'")], "groups": []}

    if not test_user or not test_password:
        return {"ok": False, "steps": [_step("inputs", False, "test username and password required")], "groups": []}

    user_dn = bind_dn_template.format(username=test_user)
    server = ldap3.Server(uri, get_info=ldap3.NONE)
    steps.append(_step("resolve bind DN", True, user_dn))

    try:
        conn = ldap3.Connection(
            server,
            user=user_dn,
            password=test_password,
            auto_bind=(ldap3.AUTO_BIND_TLS_BEFORE_BIND if start_tls else True),
        )
        steps.append(_step("StartTLS" if start_tls else "connect", True, uri))
        steps.append(_step("bind as test user", True, "authentication succeeded"))
    except Exception as e:  # noqa: BLE001 - report any failure to the operator
        steps.append(_step("bind as test user", False, str(e)))
        return {"ok": False, "steps": steps, "groups": []}

    try:
        if base_dn:
            conn.search(base_dn, user_filter.format(username=test_user), attributes=["cn", "memberOf"])
            if conn.entries:
                groups += [_cn(str(dn)) for dn in (conn.entries[0].memberOf or [])]
        if not groups and (group_base_dn or base_dn):
            conn.search(
                group_base_dn or base_dn,
                group_filter.format(user_dn=user_dn, username=test_user),
                attributes=["cn"],
            )
            groups += [str(g.cn) for g in conn.entries if g.cn]
    finally:
        conn.unbind()

    groups = list(dict.fromkeys(groups))
    steps.append(_step("resolve groups", True, ", ".join(groups) or "(none)"))
    mapped = _mapped(groups, group_permissions)
    steps.append(_step("map to permissions", bool(mapped), json.dumps(mapped) or "{}"))
    return {"ok": True, "steps": steps, "groups": groups, "mapped": mapped}


def _cn(dn: str) -> str:
    first = dn.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else dn


# ---- Entra ----
def entra_probe(*, tenant_id: str, client_id: str, client_secret: str, redirect_uri: str = "") -> dict:
    steps: list[dict] = []
    if not (tenant_id and client_id and client_secret):
        return {"ok": False, "steps": [_step("inputs", False, "tenant, client id and secret required")]}

    disco_url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    try:
        disco = httpx.get(disco_url, timeout=10)
        disco.raise_for_status()
        token_endpoint = disco.json()["token_endpoint"]
        steps.append(_step("fetch OIDC discovery", True, "tenant reachable"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "steps": [_step("fetch OIDC discovery", False, str(e))]}

    try:
        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=10,
        )
        if resp.status_code == 200 and "access_token" in resp.json():
            steps.append(_step("validate client credentials", True, "token acquired"))
            ok = True
        else:
            err = resp.json().get("error_description", resp.text)[:200]
            steps.append(_step("validate client credentials", False, err))
            ok = False
    except Exception as e:  # noqa: BLE001
        steps.append(_step("validate client credentials", False, str(e)))
        ok = False

    if redirect_uri:
        steps.append(_step("redirect URI to register", True, redirect_uri))
    return {"ok": ok, "steps": steps, "redirect_uri": redirect_uri}


def entra_decode_token(id_token: str, group_permissions: str | dict = "{}") -> dict:
    """Decode an ID token WITHOUT verifying (display only) to show its groups claim."""
    import base64

    try:
        payload = id_token.split(".")[1]
        payload += "=" * ((-len(payload)) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "steps": [_step("decode token", False, str(e))]}

    groups = claims.get("groups", [])
    if isinstance(groups, str):
        groups = [groups]
    mapped = _mapped(list(groups), group_permissions)
    return {
        "ok": True,
        "unverified": True,
        "name": claims.get("name"),
        "groups": list(groups),
        "mapped": mapped,
        "steps": [
            _step("decode token (unverified)", True, "for inspection only"),
            _step("groups claim", bool(groups), ", ".join(map(str, groups)) or "(none)"),
            _step("map to permissions", bool(mapped), json.dumps(mapped)),
        ],
    }
