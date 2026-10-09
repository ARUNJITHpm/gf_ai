"""Database engine and session helpers (sync SQLAlchemy, SQLite)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def create_db_engine(
    database_url: str, *, foreign_keys: bool = True, **engine_kwargs: Any
) -> Engine:
    """`foreign_keys=False` is for migrations only (see alembic/env.py)."""
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    # hide_parameters: SQL errors would otherwise carry message text into logs/tracebacks.
    engine_kwargs.setdefault("hide_parameters", True)
    engine = create_engine(url, **engine_kwargs)
    if url.get_backend_name() == "sqlite":

        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            # WAL lets the API read while background jobs write; SQLite ships with FKs off.
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
            cursor.close()

        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back if the handler raises."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session
