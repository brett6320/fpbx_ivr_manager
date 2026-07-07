"""Google Cloud Text-to-Speech via REST API key.

Requesting LINEAR16 @ 8000 Hz mono yields a WAV container that FreeSWITCH can
play directly — no ffmpeg/sox transcode step required.
"""
from __future__ import annotations

import base64

import httpx

from app.config import settings

_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"


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
        params={"key": settings.google_tts_api_key},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    audio_b64 = resp.json()["audioContent"]
    wav = base64.b64decode(audio_b64)
    if wav[:4] != b"RIFF":
        raise ValueError("TTS did not return a WAV container")
    return wav
