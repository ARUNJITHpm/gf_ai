"""Application settings, read from environment variables and `.env`."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    """Per-user data folder, outside the repo: %LOCALAPPDATA% on Windows, XDG elsewhere."""
    if os.name == "nt" and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "reply-assistant"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "reply-assistant"


def default_database_url() -> str:
    return f"sqlite:///{(default_data_dir() / 'data.db').as_posix()}"


class Settings(BaseSettings):
    # env_ignore_empty: "ANTHROPIC_API_KEY=" in .env means "not set", not an empty secret.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore"
    )

    # Shared secret the Android app sends in the X-API-Key header.
    api_key: SecretStr = Field(min_length=16)

    # Needed from Phase 4 (agent) onwards. LLM_PROVIDER picks which API key is used.
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_fast_model: str = "claude-haiku-5-5"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # Needed from Phase 3 (admin) onwards.
    admin_user: str = "admin"
    admin_password_hash: SecretStr | None = None
    secret_key: SecretStr | None = None
    # Mark the admin session cookie Secure. Turn on when served over HTTPS (tailscale serve).
    admin_https_only: bool = False

    database_url: str = Field(default_factory=default_database_url)
    timezone: str = "UTC"
    # Background jobs (extraction, reminders, style). Off in tests.
    enable_jobs: bool = True
    log_level: str = "INFO"

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {value!r}")
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def model_cache_dir(self) -> Path:
        """Embedding models live next to the default database, not in a temp folder."""
        return default_data_dir() / "models"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields come from the environment
