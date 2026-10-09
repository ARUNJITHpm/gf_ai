"""Edit what the assistant remembers, from the phone (spec §7 /memory/*).

Pinned facts are always sent to the model; keep them few. Notes are used only when the
conversation is related to them, so any number is fine. Notes written here are approved
immediately (I wrote them); notes from the extractor wait in the review queue.
"""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.logging_utils import log_event
from app.models import Contact, MemoryNote, PinnedFact
from app.models.enums import FactCategory, NoteOrigin, NoteStatus, Recurrence
from app.services.ingest import match_contact

router = APIRouter(prefix="/memory")

ContactHint = Annotated[str, Query(min_length=1, max_length=200, description="Chat title")]


def _contact(session: Session, hint: str) -> Contact:
    contact = match_contact(session, hint)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown contact")
    return contact


# --- pinned facts -----------------------------------------------------------------------


class FactIn(BaseModel):
    contact_hint: str = Field(min_length=1, max_length=200)
    category: FactCategory
    content: str = Field(min_length=1, max_length=2000)
    event_date: date | None = None
    recurrence: Recurrence = Recurrence.NONE


class FactPatch(BaseModel):
    category: FactCategory | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    event_date: date | None = None
    recurrence: Recurrence | None = None
    active: bool | None = None


class FactOut(BaseModel):
    id: int
    category: FactCategory
    content: str
    event_date: date | None
    recurrence: Recurrence
    active: bool
    updated_at: datetime


def _fact_out(f: PinnedFact) -> FactOut:
    return FactOut(
        id=f.id,
        category=f.category,
        content=f.content,
        event_date=f.event_date,
        recurrence=f.recurrence,
        active=f.active,
        updated_at=f.updated_at,
    )


def _check_date(category: FactCategory, event_date: date | None) -> None:
    if category == FactCategory.DATE and event_date is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A date fact needs event_date",
        )


@router.get("/pinned", response_model=list[FactOut])
def list_facts(
    contact_hint: ContactHint, session: Annotated[Session, Depends(get_session)]
) -> list[FactOut]:
    contact = _contact(session, contact_hint)
    facts = session.scalars(
        select(PinnedFact)
        .where(PinnedFact.contact_id == contact.id)
        .order_by(PinnedFact.active.desc(), PinnedFact.updated_at.desc())
    )
    return [_fact_out(f) for f in facts]


@router.post("/pinned", response_model=FactOut, status_code=status.HTTP_201_CREATED)
def create_fact(body: FactIn, session: Annotated[Session, Depends(get_session)]) -> FactOut:
    contact = _contact(session, body.contact_hint)
    _check_date(body.category, body.event_date)
    fact = PinnedFact(
        contact_id=contact.id,
        category=body.category,
        content=body.content.strip(),
        event_date=body.event_date,
        recurrence=body.recurrence,
    )
    session.add(fact)
    session.commit()
    log_event("memory.fact_created", fact_id=fact.id, category=fact.category.value)
    return _fact_out(fact)


@router.patch("/pinned/{fact_id}", response_model=FactOut)
def update_fact(
    fact_id: int, body: FactPatch, session: Annotated[Session, Depends(get_session)]
) -> FactOut:
    fact = session.get(PinnedFact, fact_id)
    if fact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown fact")
    changes = body.model_dump(exclude_unset=True)
    if "content" in changes and changes["content"] is not None:
        changes["content"] = changes["content"].strip()
    for field, value in changes.items():
        if value is not None or field == "event_date":
            setattr(fact, field, value)
    _check_date(fact.category, fact.event_date)
    session.commit()
    log_event("memory.fact_updated", fact_id=fact.id)
    return _fact_out(fact)


@router.delete("/pinned/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fact(fact_id: int, session: Annotated[Session, Depends(get_session)]) -> Response:
    fact = session.get(PinnedFact, fact_id)
    if fact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown fact")
    session.delete(fact)
    session.commit()
    log_event("memory.fact_deleted", fact_id=fact_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- notes ------------------------------------------------------------------------------


class NoteIn(BaseModel):
    contact_hint: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=2000)


class NoteOut(BaseModel):
    id: int
    content: str
    status: NoteStatus
    origin: NoteOrigin
    created_at: datetime


def _note_out(n: MemoryNote) -> NoteOut:
    return NoteOut(
        id=n.id, content=n.content, status=n.status, origin=n.origin, created_at=n.created_at
    )


def _get_note(session: Session, note_id: int) -> MemoryNote:
    note = session.get(MemoryNote, note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown note")
    return note


@router.get("/notes", response_model=list[NoteOut])
def list_notes(
    contact_hint: ContactHint,
    session: Annotated[Session, Depends(get_session)],
    status_: Annotated[NoteStatus, Query(alias="status")] = NoteStatus.APPROVED,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[NoteOut]:
    contact = _contact(session, contact_hint)
    notes = session.scalars(
        select(MemoryNote)
        .where(MemoryNote.contact_id == contact.id, MemoryNote.status == status_)
        .order_by(MemoryNote.created_at.desc(), MemoryNote.id.desc())
        .limit(limit)
    )
    return [_note_out(n) for n in notes]


@router.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(body: NoteIn, session: Annotated[Session, Depends(get_session)]) -> NoteOut:
    contact = _contact(session, body.contact_hint)
    note = MemoryNote(
        contact_id=contact.id,
        content=body.content.strip(),
        status=NoteStatus.APPROVED,  # I wrote it myself; embedded on first use
        origin=NoteOrigin.MANUAL,
    )
    session.add(note)
    session.commit()
    log_event("memory.note_created", note_id=note.id)
    return _note_out(note)


@router.post("/notes/{note_id}/approve", response_model=NoteOut)
def approve_note(note_id: int, session: Annotated[Session, Depends(get_session)]) -> NoteOut:
    note = _get_note(session, note_id)
    note.status = NoteStatus.APPROVED
    session.commit()
    log_event("memory.note_approved", note_id=note.id)
    return _note_out(note)


@router.post("/notes/{note_id}/reject", response_model=NoteOut)
def reject_note(note_id: int, session: Annotated[Session, Depends(get_session)]) -> NoteOut:
    note = _get_note(session, note_id)
    note.status = NoteStatus.REJECTED
    session.commit()
    log_event("memory.note_rejected", note_id=note.id)
    return _note_out(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(note_id: int, session: Annotated[Session, Depends(get_session)]) -> Response:
    session.delete(_get_note(session, note_id))
    session.commit()
    log_event("memory.note_deleted", note_id=note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
