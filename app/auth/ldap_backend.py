"""LDAP auth backend (optional).

Authenticates by binding to the directory as the user. Requires the 'ldap'
extra: pip install '.[ldap]'.
"""
from __future__ import annotations

import re

from app.config import settings

# Reject usernames outside a strict allowlist before they reach a DN/filter.
_USERNAME_RE = re.compile(r"[A-Za-z0-9._@\-]{1,256}")


def authenticate(username: str, password: str) -> dict | None:
    if not password:  # never allow anonymous/unauthenticated bind
        return None
    if not _USERNAME_RE.fullmatch(username):  # guard against LDAP injection
        return None
    import ldap3  # optional dep
    from ldap3.utils.conv import escape_filter_chars
    from ldap3.utils.dn import escape_rdn

    # validated above; escape again as defense in depth before DN / search filter
    dn_user = escape_rdn(username)
    filter_user = escape_filter_chars(username)

    user_dn = settings.ldap_bind_dn_template.format(username=dn_user)
    server = ldap3.Server(settings.ldap_uri, get_info=ldap3.NONE)
    try:
        conn = ldap3.Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=(ldap3.AUTO_BIND_TLS_BEFORE_BIND if settings.ldap_start_tls else True),
        )
    except ldap3.core.exceptions.LDAPException:
        return None

    display = username
    groups: list[str] = []
    try:
        if settings.ldap_base_dn:
            conn.search(
                settings.ldap_base_dn,
                settings.ldap_user_filter.format(username=filter_user),
                attributes=["cn", "displayName", "mail", "memberOf"],
            )
            if conn.entries:
                e = conn.entries[0]
                display = str(e.displayName or e.cn or username)
                groups.extend(_rdn_cn(str(dn)) for dn in (e.memberOf or []))

        # directory schemas that don't populate memberOf: search groups by member
        group_base = settings.ldap_group_base_dn or settings.ldap_base_dn
        if not groups and group_base:
            conn.search(
                group_base,
                settings.ldap_group_filter.format(
                    user_dn=escape_filter_chars(user_dn), username=filter_user
                ),
                attributes=["cn"],
            )
            groups.extend(str(g.cn) for g in conn.entries if g.cn)
    finally:
        conn.unbind()

    # de-dup, preserve order
    seen: dict[str, None] = {}
    for g in groups:
        seen.setdefault(g, None)
    return {"name": display, "email": username, "oid": user_dn, "groups": list(seen)}


def _rdn_cn(dn: str) -> str:
    """Extract the CN value from a group DN (e.g. 'cn=ivr-admins,ou=groups,...')."""
    first = dn.split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else dn
