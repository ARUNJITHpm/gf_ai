from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app import cli
from app.config import get_settings
from app.models import Base

FIXTURES = Path(__file__).parent / "fixtures" / "exports"


@pytest.fixture(autouse=True)
def cli_env(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "cli-test-key-0123456789")
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("TIMEZONE", "Asia/Kolkata")
    monkeypatch.chdir(FIXTURES)  # no .env here
    get_settings.cache_clear()
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> object:
    yield
    get_settings.cache_clear()


def test_add_import_and_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["add-contact", "--name", "Sam", "--title", "Sam 🌸"]) == 0
    assert "created contact 1" in capsys.readouterr().out

    args = ["import", "--file", str(FIXTURES / "android_dmy_24h.txt"), "--contact", "Sam"]
    args += ["--me", "Alex", "--them", "Sam"]
    assert cli.main(args) == 0
    assert "inserted=9" in capsys.readouterr().out

    assert cli.main(args) == 0
    assert "inserted=0 skipped=9" in capsys.readouterr().out

    assert cli.main(["list-contacts"]) == 0
    assert "messages=9" in capsys.readouterr().out


def test_import_errors_are_friendly(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["add-contact", "--name", "Sam", "--title", "Sam"])
    args = ["import", "--file", str(FIXTURES / "android_dmy_24h.txt"), "--contact", "1"]
    assert cli.main([*args, "--me", "Wrong", "--them", "Sam"]) == 1
    assert "authors in this file" in capsys.readouterr().err

    assert (
        cli.main(["import", "--file", "missing.txt", "--contact", "1", "--me", "a", "--them", "b"])
        == 1
    )
    assert (
        cli.main(["import", "--file", "x.txt", "--contact", "Nobody", "--me", "a", "--them", "b"])
        == 1
    )
