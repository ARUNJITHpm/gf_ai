"""Alembic environment. The DB URL comes from app settings unless set on the Config."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text

from app.config import get_settings
from app.db import create_db_engine
from app.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Foreign keys OFF while migrating: batch mode drops and recreates tables, and with
    # FKs on, dropping a parent table would cascade-delete its children's rows.
    engine = create_db_engine(_database_url(), foreign_keys=False, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite can't ALTER most things; batch mode recreates the table instead.
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        if connection.dialect.name == "sqlite":
            problems = connection.execute(text("PRAGMA foreign_key_check")).fetchall()
            if problems:
                raise RuntimeError(f"foreign key violations after migration: {problems}")
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
