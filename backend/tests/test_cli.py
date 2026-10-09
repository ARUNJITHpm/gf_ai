from pathlib import Path

import bcrypt
import pytest
from sqlalchemy import create_engine

from app import cli
from app.config import Settings, get_settings
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


def test_hash_password_output_works_in_env_file(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    answers = iter(["a-long-password", "a-long-password"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["hash-password"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith("ADMIN_PASSWORD_HASH='$2")

    # The printed line, pasted into .env, must load back unchanged ("$" not expanded).
    env = tmp_path / ".env"
    env.write_text(f"API_KEY=cli-test-key-0123456789\n{line}\n", encoding="utf-8")
    loaded = Settings(_env_file=env)  # type: ignore[call-arg]
    assert loaded.admin_password_hash is not None
    stored = loaded.admin_password_hash.get_secret_value().encode()
    assert bcrypt.checkpw(b"a-long-password", stored)


def test_hash_password_rejects_mismatch_and_short(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter(["a-long-password", "different-password", "short"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["hash-password"]) == 1
    assert cli.main(["hash-password"]) == 1


def test_suggest_prints_replies(capsys: pytest.CaptureFixture[str]) -> None:
    from argparse import Namespace

    from app.agent.llm import FakeLLM
    from app.agent.schemas import DraftReply, DraftSet, RiskReport, RiskVerdict
    from app.db import create_db_engine, create_session_factory

    cli.main(["add-contact", "--name", "Sam", "--title", "Sam"])
    capsys.readouterr()
    fake = FakeLLM()
    reply = DraftReply(
        text="hey you", tone="warm", reason="short", used_fact_ids=[], used_note_ids=[]
    )
    fake.queue(DraftSet, DraftSet(replies=[reply, reply, reply]))
    fake.queue(
        RiskReport, RiskReport(verdicts=[RiskVerdict(index=0, ok=True, problem="", rewrite="")])
    )

    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    with create_session_factory(engine)() as session:
        args = Namespace(contact="Sam", tone=None, draft=None)
        assert cli._suggest(session, args, settings, llm=fake) == 0
    engine.dispose()
    out = capsys.readouterr().out
    assert out.count("hey you") == 3
    assert "suggestion #1:" in out
