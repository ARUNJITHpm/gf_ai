from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient, LLMError, make_llm
from app.agent.nodes import DraftFailed
from app.config import Settings
from app.db import get_session
from app.logging_utils import log_event
from app.schemas.suggest import Suggestion, SuggestRequest, SuggestResponse
from app.security import RateLimiter
from app.services.ingest import IncomingMessage, ingest_messages, match_contact
from app.services.suggest import suggest_replies

router = APIRouter()


def get_llm(request: Request) -> LLMClient:
    """The app's LLM client, created on first use. Tests put a FakeLLM in app.state.llm."""
    llm: LLMClient | None = getattr(request.app.state, "llm", None)
    if llm is None:
        settings: Settings = request.app.state.settings
        llm = make_llm(settings)
        request.app.state.llm = llm
    return llm


@router.post(
    "/suggest",
    response_model=SuggestResponse,
    dependencies=[Depends(RateLimiter(rate=10, per=60))],
)
def suggest(
    body: SuggestRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    llm: Annotated[LLMClient, Depends(get_llm)],
) -> SuggestResponse:
    """Ingest what's on screen, then return reply suggestions. Never sends anything."""
    contact = match_contact(session, body.contact_hint)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown contact")

    if body.visible_messages:
        ingest_messages(
            session,
            contact,
            [
                IncomingMessage(sender=m.sender, text=m.text, source=m.source, sent_at=m.sent_at)
                for m in body.visible_messages
            ],
        )

    settings: Settings = request.app.state.settings
    try:
        result = suggest_replies(
            session,
            contact,
            llm=llm,
            settings=settings,
            tone=body.tone,
            draft_text=body.draft_text,
            embedder=request.app.state.embedder,
        )
    except DraftFailed as exc:
        log_event("suggest.failed", contact_id=contact.id, reason="invalid_output")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model returned unusable suggestions. Try again.",
        ) from exc
    except LLMError as exc:
        log_event("suggest.failed", contact_id=contact.id, reason="llm_unavailable")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Suggestions are unavailable right now ({exc}).",
        ) from exc

    return SuggestResponse(
        suggestion_id=result.suggestion_id,
        suggestions=[Suggestion(**s) for s in result.suggestions],
        warnings=result.warnings,
    )
