"""WebAuthn / passkey MFA (optional).

Wraps the `webauthn` (py_webauthn) library. Install the extra:
    pip install '.[passkey]'

Credentials are stored per local user in the webauthn_credentials table
(credential id + COSE public key, base64url-encoded). The relying-party id and
origin are derived from APP_BASE_URL.
"""
from __future__ import annotations

from app.auth import local
from app.config import settings


def _lib():
    import webauthn
    from webauthn.helpers import bytes_to_base64url, base64url_to_bytes
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    return webauthn, bytes_to_base64url, base64url_to_bytes, (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )


def registration_options(username: str, display_name: str) -> tuple[str, str]:
    """Return (options_json_for_browser, challenge_b64url_to_store_in_session)."""
    webauthn, b2b64, _, structs = _lib()
    (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
     ResidentKeyRequirement, UserVerificationRequirement) = structs

    exclude = [
        PublicKeyCredentialDescriptor(id=_b64(c["credential_id"]))
        for c in local.get_credentials(username)
    ]
    opts = webauthn.generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.mfa_issuer,
        user_name=username,
        user_display_name=display_name,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return webauthn.options_to_json(opts), b2b64(opts.challenge)


def verify_registration(username: str, credential_json: str, expected_challenge_b64: str, label: str = "") -> None:
    webauthn, b2b64, b64_2b, _ = _lib()
    result = webauthn.verify_registration_response(
        credential=credential_json,
        expected_challenge=b64_2b(expected_challenge_b64),
        expected_rp_id=settings.webauthn_rp_id,
        expected_origin=settings.webauthn_origin,
        require_user_verification=True,
    )
    local.add_credential(
        username,
        credential_id=b2b64(result.credential_id),
        public_key=b2b64(result.credential_public_key),
        sign_count=result.sign_count,
        label=label,
    )


def authentication_options(username: str) -> tuple[str, str]:
    webauthn, b2b64, _, structs = _lib()
    PublicKeyCredentialDescriptor = structs[1]
    UserVerificationRequirement = structs[3]
    allow = [
        PublicKeyCredentialDescriptor(id=_b64(c["credential_id"]))
        for c in local.get_credentials(username)
    ]
    opts = webauthn.generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return webauthn.options_to_json(opts), b2b64(opts.challenge)


def verify_authentication(username: str, credential_json: str, expected_challenge_b64: str) -> bool:
    """Verify an assertion for `username`. Returns True and bumps the sign count."""
    webauthn, b2b64, b64_2b, _ = _lib()
    import json

    cred_id = json.loads(credential_json)["id"]
    stored = local.get_credential(cred_id)
    if not stored or stored["username"] != username:
        return False
    try:
        result = webauthn.verify_authentication_response(
            credential=credential_json,
            expected_challenge=b64_2b(expected_challenge_b64),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=b64_2b(stored["public_key"]),
            credential_current_sign_count=stored["sign_count"],
            require_user_verification=True,
        )
    except Exception:
        return False
    local.update_sign_count(cred_id, result.new_sign_count)
    return True


def _b64(s: str):
    from webauthn.helpers import base64url_to_bytes

    return base64url_to_bytes(s)
