from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.ingest import IngestRequest, IngestResponse
from app.security import RateLimiter
from app.services.ingest import IncomingMessage, ingest_messages, match_contact

router = APIRouter()


@router.post(
    "/ingest",
    response_model=IngestResponse,
    dependencies=[Depends(RateLimiter(rate=120, per=60))],
)
def ingest(
    body: IngestRequest, session: Annotated[Session, Depends(get_session)]
) -> IngestResponse:
    """Store messages seen on the phone (notifications, screen reads). Duplicates are skipped."""
    contact = match_contact(session, body.contact_hint)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown contact")

    result = ingest_messages(
        session,
        contact,
        [
            IncomingMessage(sender=m.sender, text=m.text, source=m.source, sent_at=m.sent_at)
            for m in body.messages
        ],
    )
    return IngestResponse(
        contact_id=contact.id,
        inserted=result.inserted,
        skipped=result.skipped,
        updated=result.updated,
    )
