from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_loads_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "API_KEY=from-env-file-0123456789\n"
        "TIMEZONE=Asia/Kolkata\n"
        "LOG_LEVEL=debug\n"
        "ANTHROPIC_API_KEY=\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)  # type: ignore[call-arg]
    assert settings.api_key.get_secret_value() == "from-env-file-0123456789"
    assert settings.timezone == "Asia/Kolkata"
    assert settings.log_level == "DEBUG"
    # An empty value in .env means "not set".
    assert settings.anthropic_api_key is None


def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ValidationError, match="api_key"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_short_api_key_rejected() -> None:
    with pytest.raises(ValidationError, match="api_key"):
        Settings(_env_file=None, api_key="short")  # type: ignore[arg-type]


def test_unknown_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Settings(_env_file=None, api_key="x" * 16, timezone="Mars/Olympus")  # type: ignore[arg-type]


def test_default_database_is_outside_the_repo() -> None:
    settings = Settings(_env_file=None, api_key="x" * 16)  # type: ignore[arg-type]
    assert settings.database_url.startswith("sqlite:///")
    assert "reply-assistant/data.db" in settings.database_url
    repo_root = Path(__file__).resolve().parents[2].as_posix()
    assert repo_root not in settings.database_url
