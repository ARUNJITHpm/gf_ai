"""App factory. Run with: uv run uvicorn app.main:create_app --factory --host 127.0.0.1"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app import __version__
from app.api import auth, health
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.logging_utils import configure_logging, log_event
from app.security import require_api_key


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log_event("app.start", version=__version__)
        yield
        engine.dispose()
        log_event("app.stop")

    app = FastAPI(title="Reply Assistant", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    # Public: only /health (and the OpenAPI docs, which contain no data).
    app.include_router(health.router)

    # Everything else requires X-API-Key. Add new routers here.
    protected = APIRouter(dependencies=[Depends(require_api_key)])
    protected.include_router(auth.router)
    app.include_router(protected)
    return app
