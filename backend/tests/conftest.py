from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.memory.embeddings import FakeEmbedder
from app.models import Base

TEST_API_KEY = "test-api-key-0123456789"


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def settings(db_url: str) -> Settings:
    # _env_file=None: tests must never read the developer's real .env.
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        api_key=TEST_API_KEY,  # type: ignore[arg-type]
        database_url=db_url,
        timezone="Asia/Kolkata",  # same as the real setup; exercises local -> UTC conversion
        enable_jobs=False,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    app.state.embedder = FakeEmbedder()  # never download the real model in tests
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def session(app: FastAPI) -> Iterator[Session]:
    with app.state.session_factory() as session:
        yield session
