from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import Base

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(db_url: str) -> Config:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    config.attributes["configure_logger"] = False
    return config


def test_upgrade_creates_all_tables_and_downgrade_drops_them(db_url: str) -> None:
    config = alembic_config(db_url)
    engine = create_engine(db_url)

    command.upgrade(config, "head")
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_migrations_match_models(db_url: str) -> None:
    """Fails if a model changed without a new migration."""
    command.upgrade(alembic_config(db_url), "head")
    engine = create_engine(db_url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_migrated_schema_enforces_enum_checks(db_url: str) -> None:
    command.upgrade(alembic_config(db_url), "head")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO contact (name, wa_chat_titles, nicknames, active, created_at,"
                " updated_at) VALUES ('t', '[]', '[]', 1, '2026-01-01', '2026-01-01')"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO memory_note (contact_id, content, status, origin,"
                " source_message_ids, created_at, updated_at)"
                " VALUES (1, 'x', 'bogus', 'manual', '[]', '2026-01-01', '2026-01-01')"
            )
        )
    engine.dispose()
