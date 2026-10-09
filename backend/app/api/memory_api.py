"""Feedback on suggestions, and reminders for the phone (spec §7)."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.logging_utils import log_event
from app.models import Reminder, SuggestionLog
from app.models.base import utcnow
from app.models.enums import ReminderOrigin

router = APIRouter()


class FeedbackRequest(BaseModel):
    chosen_index: int = Field(ge=0)
    final_text: str | None = Field(default=None, max_length=20_000)


class FeedbackResponse(BaseModel):
    suggestion_id: int
    chosen_index: int
    final_text_recorded: bool


@router.post("/suggestions/{suggestion_id}/feedback", response_model=FeedbackResponse)
def suggestion_feedback(
    suggestion_id: int,
    body: FeedbackRequest,
    session: Annotated[Session, Depends(get_session)],
) -> FeedbackResponse:
    """The phone reports which suggestion I tapped. `final_text` is optional: the linker
    job fills it in when the sent message shows up."""
    log = session.get(SuggestionLog, suggestion_id)
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown suggestion")
    if body.chosen_index >= len(log.suggestions):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"chosen_index must be below {len(log.suggestions)}",
        )
    log.chosen_index = body.chosen_index
    if body.final_text is not None and body.final_text.strip():
        log.final_text = body.final_text
    session.commit()
    log_event("suggest.feedback", suggestion_id=log.id, chosen_index=body.chosen_index)
    return FeedbackResponse(
        suggestion_id=log.id,
        chosen_index=body.chosen_index,
        final_text_recorded=log.final_text is not None,
    )


class ReminderOut(BaseModel):
    id: int
    contact_id: int
    title: str
    due_at: datetime
    origin: ReminderOrigin


@router.get("/reminders/due", response_model=list[ReminderOut])
def reminders_due(
    session: Annotated[Session, Depends(get_session)],
    within_hours: Annotated[int, Query(ge=0, le=24 * 30)] = 24,
) -> list[ReminderOut]:
    """Open reminders due within the next `within_hours` (overdue ones included)."""
    horizon = utcnow() + timedelta(hours=within_hours)
    rows = session.scalars(
        select(Reminder)
        .where(Reminder.done.is_(False), Reminder.due_at <= horizon)
        .order_by(Reminder.due_at)
    ).all()
    return [
        ReminderOut(
            id=r.id, contact_id=r.contact_id, title=r.title, due_at=r.due_at, origin=r.origin
        )
        for r in rows
    ]


@router.post("/reminders/{reminder_id}/done", response_model=ReminderOut)
def reminder_done(
    reminder_id: int, session: Annotated[Session, Depends(get_session)]
) -> ReminderOut:
    reminder = session.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown reminder")
    reminder.done = True
    session.commit()
    return ReminderOut(
        id=reminder.id,
        contact_id=reminder.contact_id,
        title=reminder.title,
        due_at=reminder.due_at,
        origin=reminder.origin,
    )
