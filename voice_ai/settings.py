"""Runtime configuration, read from environment variables.

Every value has a safe default so the server can start for local development
without any configuration. See `.env.example` for documentation of each variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal `.env` loader (KEY=VALUE lines); existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {value!r}") from exc


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    whisper_model: str = "large-v3"
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    admin_token: str = ""
    cors_origins: list[str] = field(default_factory=list)
    max_upload_mb: int = 10
    piper_voice_dir: Path = BASE_DIR / "piper_voices"
    load_models: bool = True

    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        _load_dotenv(BASE_DIR / ".env")
        voice_dir = os.environ.get("PIPER_VOICE_DIR")
        return cls(
            gemini_api_key=os.environ.get("GEMINI_API_KEY", "").strip(),
            gemini_model=os.environ.get("GEMINI_MODEL", cls.gemini_model),
            whisper_model=os.environ.get("WHISPER_MODEL", cls.whisper_model),
            redis_host=os.environ.get("REDIS_HOST", cls.redis_host),
            redis_port=_env_int("REDIS_PORT", cls.redis_port),
            redis_db=_env_int("REDIS_DB", cls.redis_db),
            admin_token=os.environ.get("ADMIN_TOKEN", "").strip(),
            cors_origins=_env_list("CORS_ORIGINS", []),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", cls.max_upload_mb),
            piper_voice_dir=Path(voice_dir) if voice_dir else BASE_DIR / "piper_voices",
            load_models=_env_bool("LOAD_MODELS", True),
        )


settings = Settings.from_env()
