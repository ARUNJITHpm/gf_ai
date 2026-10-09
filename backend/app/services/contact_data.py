"""Count and delete everything stored for one contact (the contact row itself is kept)."""

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.logging_utils import log_event
from app.models import (
    MemoryNote,
    Message,
    PinnedFact,
    Reminder,
    StyleProfile,
    SuggestionLog,
)
from app.models.base import Base

# Children before parents: messages point at suggestion logs.
_TABLES: list[type[Base]] = [
    Message,
    SuggestionLog,
    MemoryNote,
    PinnedFact,
    StyleProfile,
    Reminder,
]


def count_contact_data(session: Session, contact_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model in _TABLES:
        column = model.__table__.c.contact_id
        total = session.scalar(select(func.count()).select_from(model).where(column == contact_id))
        counts[model.__tablename__] = total or 0
    return counts


def delete_contact_data(session: Session, contact_id: int) -> dict[str, int]:
    """Delete all messages, notes, facts, style profiles, logs and reminders. Commits."""
    counts = count_contact_data(session, contact_id)
    # Unlink first so deleting suggestion logs never depends on FK cascade order.
    session.execute(
        update(Message).where(Message.contact_id == contact_id).values(linked_suggestion_id=None)
    )
    for model in _TABLES:
        column = model.__table__.c.contact_id
        session.execute(delete(model).where(column == contact_id))
    session.commit()
    log_event(
        "contact_data.deleted",
        contact_id=contact_id,
        **{f"deleted_{table}": n for table, n in counts.items()},
    )
    return counts
