from app.config import settings


def test_secure_derived_from_https_base_url(monkeypatch):
    monkeypatch.setattr(settings, "session_https_only_raw", "")
    monkeypatch.setattr(settings, "app_base_url", "https://ivr.example.com")
    assert settings.session_https_only is True


def test_secure_derived_off_for_http_base_url(monkeypatch):
    monkeypatch.setattr(settings, "session_https_only_raw", "")
    monkeypatch.setattr(settings, "app_base_url", "http://pbx.local:8080")
    assert settings.session_https_only is False  # so login works over plain HTTP


def test_explicit_override(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://ivr.example.com")
    monkeypatch.setattr(settings, "session_https_only_raw", "false")
    assert settings.session_https_only is False
    monkeypatch.setattr(settings, "app_base_url", "http://pbx.local")
    monkeypatch.setattr(settings, "session_https_only_raw", "true")
    assert settings.session_https_only is True
