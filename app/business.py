"""Admin-managed business profile: a business name plus arbitrary named
templates (reusable text snippets — a generic greeting, a closing line, hours,
etc.), used for placeholder substitution in prompts.

Stored as JSON in the app's writable state dir (BUSINESS_CONFIG_FILE). Prompts
(and templates themselves) reference placeholders that are plugged in before TTS:

  {business_name}   -> the configured business name (falls back to APP_ORG_NAME)
  {<template>}      -> the value of a named template, e.g. {greeting}, {closing}

Templates may reference other templates (nested templating) — those are resolved
recursively, with a cycle guard. Unknown placeholders are left untouched.
"""
from __future__ import annotations

import json
import os
import re

from app.config import settings

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.-]+)\}")
_MAX_DEPTH = 10


def _path() -> str:
    return settings.business_config_file


def load() -> dict:
    try:
        with open(_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


# default call destinations, keyed by these names (numbers/targets, not secret)
DESTINATION_KEYS = ("on_hours", "off_hours", "emergency")


def save(
    business_name: str,
    templates: dict[str, str],
    *,
    closure_opening: str = "",
    closure_closing: str = "",
    destinations: dict[str, str] | None = None,
    footer_text: str = "",
    login_notice: str = "",
) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    dests = destinations or {}
    payload = {
        "business_name": (business_name or "").strip(),
        "templates": {k.strip(): v.strip() for k, v in templates.items() if k.strip()},
        "closure_opening": (closure_opening or "").strip(),
        "closure_closing": (closure_closing or "").strip(),
        "destinations": {k: (dests.get(k) or "").strip() for k in DESTINATION_KEYS},
        # shown site-wide (footer) and on the login page (security notice)
        "footer_text": (footer_text or "").strip(),
        "login_notice": (login_notice or "").strip(),
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)


def default_destinations() -> dict[str, str]:
    """The configured default destinations (on_hours/off_hours/emergency)."""
    d = load().get("destinations")
    d = d if isinstance(d, dict) else {}
    return {k: (d.get(k) or "") for k in DESTINATION_KEYS}


def footer_text() -> str:
    """Optional footer text shown on every page (admin-configurable)."""
    return load().get("footer_text") or ""


def login_notice() -> str:
    """Optional security notice shown on the login page (admin-configurable)."""
    return load().get("login_notice") or ""


def default_destination(name: str) -> str:
    return default_destinations().get(name, "")


def business_name() -> str:
    return load().get("business_name") or settings.app_org_name


def closure_opening() -> str:
    """Admin-configured opening line of the closure greeting (empty = default)."""
    v = load().get("closure_opening")
    return v.strip() if isinstance(v, str) else ""


def closure_closing() -> str:
    """Admin-configured closing line of the closure greeting (empty = default)."""
    v = load().get("closure_closing")
    return v.strip() if isinstance(v, str) else ""


def templates() -> dict[str, str]:
    data = load()
    t = data.get("templates")
    if not isinstance(t, dict):
        t = data.get("hours_templates")  # back-compat with the earlier schema
    return t if isinstance(t, dict) else {}


def placeholders() -> dict[str, str]:
    """Placeholder key -> value. business_name is a reserved built-in."""
    return {**templates(), "business_name": business_name()}


def placeholder_keys() -> list[str]:
    """Keys to show as hints in the UI, e.g. {business_name}, {greeting}."""
    return [f"{{{k}}}" for k in placeholders()]


def _render(text: str, ph: dict[str, str], seen: frozenset[str], depth: int) -> str:
    if not text or depth > _MAX_DEPTH:
        return text or ""

    def sub(m: re.Match) -> str:
        key = m.group(1).strip()
        if key not in ph or key in seen:  # unknown or cycle -> leave as-is
            return m.group(0)
        return _render(ph[key], ph, seen | {key}, depth + 1)

    return _PLACEHOLDER.sub(sub, text)


def render(text: str | None) -> str:
    """Substitute {placeholders} in text, resolving nested templates recursively;
    leaves unknown placeholders (and cycles) untouched."""
    if not text:
        return text or ""
    return _render(text, placeholders(), frozenset(), 0)
