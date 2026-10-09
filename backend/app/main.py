"""App factory. Run with: uv run uvicorn app.main:create_app --factory --host 127.0.0.1"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.exc import OperationalError

from app import __version__
from app.admin.setup import mount_admin
from app.api import auth, health, importer, ingest, memory_api, suggest
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.logging_utils import configure_logging, log_event
from app.memory.embeddings import FastEmbedEmbedder
from app.security import require_api_key
from app.services.settings_store import seed_defaults


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log_event("app.start", version=__version__)
        try:
            with app.state.session_factory() as session:
                seed_defaults(session)
        except OperationalError:
            log_event("app.db_not_migrated", hint="run: uv run alembic upgrade head")
        app.state.scheduler = start_scheduler(app)
        yield
        stop_scheduler(app)
        engine.dispose()
        log_event("app.stop")

    app = FastAPI(title="Reply Assistant", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.llm = None  # created on first /suggest (see app.api.suggest.get_llm)
    # Cheap to create; the model itself loads on first use.
    app.state.embedder = FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir)

    # Public: only /health (and the OpenAPI docs, which contain no data).
    app.include_router(health.router)

    # Everything else requires X-API-Key. Add new routers here.
    protected = APIRouter(dependencies=[Depends(require_api_key)])
    protected.include_router(auth.router)
    protected.include_router(ingest.router)
    protected.include_router(importer.router)
    protected.include_router(suggest.router)
    protected.include_router(memory_api.router)
    app.include_router(protected)

    # /admin uses its own login (session cookie), not the API key.
    mount_admin(app, settings)
    return app
