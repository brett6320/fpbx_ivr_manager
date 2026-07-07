"""FreeSWITCH mod_xml_rpc client — the only native runtime interconnect.

Used to make FreeSWITCH pick up DB/dialplan changes (reloadxml) and to run
diagnostic API commands. It does NOT model config; that is done via the DB.
"""
from __future__ import annotations

import xmlrpc.client

from app.config import settings


def _proxy() -> xmlrpc.client.ServerProxy:
    # inject basic-auth creds into the URL: http://user:pass@host:8787/RPC2
    url = settings.fs_xmlrpc_url
    scheme, rest = url.split("://", 1)
    auth = f"{settings.fs_xmlrpc_user}:{settings.fs_xmlrpc_password}@"
    return xmlrpc.client.ServerProxy(f"{scheme}://{auth}{rest}")


def api(command: str, arg: str = "") -> str:
    """Run a FreeSWITCH API command, e.g. api('reloadxml')."""
    return _proxy().freeswitch.api(command, arg)  # type: ignore[attr-defined]


def reloadxml() -> bool:
    out = api("reloadxml")
    return "+OK" in out or "Success" in out
