import pytest

from voice_ai.settings import Settings


def test_defaults(monkeypatch):
    for name in ("GEMINI_API_KEY", "ADMIN_TOKEN", "CORS_ORIGINS", "REDIS_PORT", "LOAD_MODELS"):
        monkeypatch.delenv(name, raising=False)
    s = Settings.from_env()
    assert s.llm_configured is False
    assert s.redis_port == 6379
    assert s.cors_origins == []
    assert s.max_upload_bytes == 10 * 1024 * 1024


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("LOAD_MODELS", "false")
    s = Settings.from_env()
    assert s.llm_configured
    assert s.cors_origins == ["https://a.example", "https://b.example"]
    assert s.redis_port == 6380
    assert s.load_models is False


def test_invalid_integer(monkeypatch):
    monkeypatch.setenv("REDIS_PORT", "abc")
    with pytest.raises(ValueError, match="REDIS_PORT"):
        Settings.from_env()
