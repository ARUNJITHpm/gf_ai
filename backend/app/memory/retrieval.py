"""Which approved notes go into the prompt.

Phase 4 version: the most recently updated approved notes. Phase 5 (P5.3) replaces the body
with embedding similarity against the latest messages; the signature stays the same.
Only `approved` notes are ever returned.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MemoryNote, Message
from app.models.enums import NoteStatus


def retrieve_notes(
    session: Session, contact_id: int, recent_messages: Sequence[Message], *, top_k: int
) -> list[MemoryNote]:
    if top_k <= 0:
        return []
    notes = session.scalars(
        select(MemoryNote)
        .where(MemoryNote.contact_id == contact_id, MemoryNote.status == NoteStatus.APPROVED)
        .order_by(MemoryNote.updated_at.desc(), MemoryNote.id.desc())
        .limit(top_k)
    )
    return list(notes)
