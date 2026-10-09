from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import __version__
from app.db import get_session

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    db: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health(session: Annotated[Session, Depends(get_session)]) -> HealthResponse:
    """Unauthenticated liveness check."""
    try:
        session.execute(text("SELECT 1"))
        db = "ok"
    except SQLAlchemyError:
        db = "error"
    return HealthResponse(status="ok" if db == "ok" else "degraded", db=db, version=__version__)
