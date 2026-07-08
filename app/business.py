"""Admin-managed business profile: business name + reusable business-hours
templates, plus placeholder substitution for prompt text.

Stored as JSON in the app's writable state dir (BUSINESS_CONFIG_FILE). Prompts
(greetings) can reference placeholders that are plugged in before TTS:

  {business_name}      -> the configured business name (falls back to APP_ORG_NAME)
  {hours.<Template>}   -> the text of the named business-hours template
  {business_hours}     -> the default template's text (first one), if any
"""
from __future__ import annotations

import json
import os
import re

from app.config import settings

_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def _path() -> str:
    return settings.business_config_file


def load() -> dict:
    try:
        with open(_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save(business_name: str, hours_templates: dict[str, str]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "business_name": (business_name or "").strip(),
        "hours_templates": {k.strip(): v.strip() for k, v in hours_templates.items() if k.strip()},
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f, indent=2)


def business_name() -> str:
    return load().get("business_name") or settings.app_org_name


def hours_templates() -> dict[str, str]:
    t = load().get("hours_templates")
    return t if isinstance(t, dict) else {}


def placeholders() -> dict[str, str]:
    """The map of available placeholder keys -> values."""
    out = {"business_name": business_name()}
    tmpl = hours_templates()
    for name, text in tmpl.items():
        out[f"hours.{name}"] = text
    if tmpl:
        out["business_hours"] = next(iter(tmpl.values()))  # default = first template
    return out


def placeholder_keys() -> list[str]:
    """Keys to show as hints in the UI (e.g. {business_name}, {hours.Standard})."""
    return [f"{{{k}}}" for k in placeholders()]


def render(text: str | None) -> str:
    """Substitute known {placeholders} in text; leave unknown ones untouched."""
    if not text:
        return text or ""
    ph = placeholders()
    return _PLACEHOLDER.sub(lambda m: ph.get(m.group(1).strip(), m.group(0)), text)
