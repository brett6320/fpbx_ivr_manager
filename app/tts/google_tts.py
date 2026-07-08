"""Google Cloud Text-to-Speech via REST, authenticated with a service account.

A short-lived OAuth access token is minted from the service-account JSON
(GOOGLE_TTS_CREDENTIALS_FILE) and sent as a Bearer token — no API keys.
Requesting LINEAR16 @ 8000 Hz mono yields a WAV container that FreeSWITCH can
play directly, so no ffmpeg/sox transcode step is required.
"""
from __future__ import annotations

import base64
from functools import lru_cache

import httpx

from app.config import settings

_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


@lru_cache(maxsize=1)
def _credentials():
    if not settings.google_tts_credentials_file:
        raise RuntimeError(
            "GOOGLE_TTS_CREDENTIALS_FILE is not set — a Google service-account JSON "
            "is required for Text-to-Speech"
        )
    from google.oauth2 import service_account  # optional dep, only when TTS is used

    return service_account.Credentials.from_service_account_file(
        settings.google_tts_credentials_file, scopes=[_SCOPE]
    )


def _access_token() -> str:
    import google.auth.transport.requests

    creds = _credentials()
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def synthesize(text: str) -> bytes:
    """Return 8 kHz mono 16-bit WAV bytes for the given text."""
    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": settings.google_tts_language,
            "name": settings.google_tts_voice,
        },
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": 8000,
        },
    }
    resp = httpx.post(
        _ENDPOINT,
        headers={"Authorization": f"Bearer {_access_token()}"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    audio_b64 = resp.json()["audioContent"]
    wav = base64.b64decode(audio_b64)
    if wav[:4] != b"RIFF":
        raise ValueError("TTS did not return a WAV container")
    return wav
