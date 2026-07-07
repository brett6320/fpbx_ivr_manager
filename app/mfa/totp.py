"""RFC 6238 TOTP (SHA-1, 6 digits, 30s) — stdlib only.

Compatible with Google Authenticator, Authy, 1Password, etc.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

_DIGITS = 6
_PERIOD = 30


def generate_secret() -> str:
    """Return a base32 secret (no padding) suitable for otpauth URIs."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _b32decode(secret: str) -> bytes:
    pad = "=" * ((-len(secret)) % 8)
    return base64.b32decode(secret.upper() + pad, casefold=True)


def _hotp(secret: str, counter: int) -> str:
    mac = hmac.new(_b32decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF) % (10 ** _DIGITS)
    return str(code).zfill(_DIGITS)


def now_code(secret: str, at: float | None = None) -> str:
    counter = int((time.time() if at is None else at) // _PERIOD)
    return _hotp(secret, counter)


def verify(secret: str, code: str, window: int = 1, at: float | None = None) -> bool:
    """Validate a code, tolerating +/- `window` steps of clock drift."""
    if not secret or not code:
        return False
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    counter = int((time.time() if at is None else at) // _PERIOD)
    return any(
        hmac.compare_digest(_hotp(secret, counter + i), code.zfill(_DIGITS))
        for i in range(-window, window + 1)
    )


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """otpauth:// URI for QR enrollment."""
    # keep the issuer:account colon literal (convention); encode each part
    label = f"{quote(issuer, safe='')}:{quote(account, safe='')}"
    params = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": _DIGITS, "period": _PERIOD}
    )
    return f"otpauth://totp/{label}?{params}"
