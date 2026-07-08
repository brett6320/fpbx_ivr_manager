import base64

from app.tts import google_tts


def test_synthesize_uses_service_account_bearer_token(monkeypatch):
    monkeypatch.setattr(google_tts, "_access_token", lambda: "tok-123")

    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"audioContent": base64.b64encode(b"RIFF....WAVE").decode()}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(google_tts.httpx, "post", fake_post)

    wav = google_tts.synthesize("hello world")

    assert wav[:4] == b"RIFF"
    assert captured["headers"]["Authorization"] == "Bearer tok-123"   # service-account token
    assert "key=" not in captured["url"] and "?" not in captured["url"]  # no API key
    assert captured["json"]["audioConfig"]["sampleRateHertz"] == 8000


def test_access_token_requires_credentials_file(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "google_tts_credentials_file", "")
    google_tts._credentials.cache_clear()
    import pytest

    with pytest.raises(RuntimeError):
        google_tts._access_token()
