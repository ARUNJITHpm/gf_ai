"""Extractor job: propose durable facts from new messages as *pending* notes (spec §9).

Runs when 50+ new text messages are waiting, or when the oldest waiting one is a day old.
Never approves anything; the admin review queue decides.
"""

from collections.abc import Sequence
from datetime import timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import prompts
from app.agent.schemas import ExtractedNote, ExtractedNotes
from app.jobs.base import JobContext, by_contact, chunked, get_state
from app.memory.embeddings import Embedder, from_blob, to_blob
from app.memory.retrieval import ensure_embeddings
from app.models import Contact, MemoryNote, Message
from app.models.enums import MessageKind, NoteOrigin

JOB = "extractor"
MIN_NEW = 50
MAX_WAIT = timedelta(days=1)
CHUNK = 80
KNOWN_NOTES_IN_PROMPT = 100
DUPLICATE_SIMILARITY = 0.9


def run(ctx: JobContext) -> str:
    with ctx.session_factory() as session:
        state = get_state(session, JOB)
        watermark = state.last_message_id or 0
        newest = session.scalar(select(func.max(Message.id))) or 0
        new = session.scalars(
            select(Message)
            .where(Message.id > watermark, Message.kind == MessageKind.TEXT)
            .order_by(Message.id)
        ).all()
        if not new:
            state.last_message_id = newest
            session.commit()
            return "no new messages"
        oldest_wait = ctx.now - new[0].observed_at
        if len(new) < MIN_NEW and oldest_wait < MAX_WAIT:
            return f"waiting ({len(new)} new messages)"

        inserted = 0
        for contact_id, messages in by_contact(new).items():
            contact = session.get(Contact, contact_id)
            if contact is None:
                continue
            existing = list(
                session.scalars(
                    select(MemoryNote)
                    .where(MemoryNote.contact_id == contact_id)
                    .order_by(MemoryNote.id)
                )
            )
            for chunk in chunked(messages, CHUNK):
                result = ctx.llm.complete_json(
                    model=ctx.settings.llm_fast_model,
                    system=[prompts.EXTRACT_SYSTEM],
                    prompt=prompts.render_extract_prompt(
                        contact=contact,
                        messages=chunk,
                        known_notes=[n.content for n in existing[-KNOWN_NOTES_IN_PROMPT:]],
                        tz=ctx.settings.tz,
                    ),
                    schema=ExtractedNotes,
                    effort="low",
                    max_tokens=4000,
                )
                if result.parsed is None:
                    continue  # unusable output: skip this chunk rather than retry forever
                inserted += add_candidates(
                    session,
                    contact_id,
                    result.parsed.notes,
                    valid_ids={m.id for m in chunk},
                    existing=existing,
                    embedder=ctx.embedder,
                )

        state.last_message_id = newest
        session.commit()  # notes and watermark together
        return f"inserted {inserted} pending notes from {len(new)} messages"


def add_candidates(
    session: Session,
    contact_id: int,
    candidates: Sequence[ExtractedNote],
    *,
    valid_ids: set[int],
    existing: list[MemoryNote],
    embedder: Embedder,
) -> int:
    """Insert candidates that aren't near-duplicates of existing notes (any status)."""
    candidates = [c for c in candidates if c.content.strip()]
    if not candidates:
        return 0
    ensure_embeddings(existing, embedder)
    known = [from_blob(n.embedding) for n in existing if n.embedding is not None]
    known_texts = {n.content.strip().casefold() for n in existing}
    vectors = embedder.embed_passages([c.content.strip() for c in candidates])

    inserted = 0
    for candidate, vector in zip(candidates, vectors, strict=True):
        text = candidate.content.strip()
        if text.casefold() in known_texts:
            continue
        if known and float(np.max(np.stack(known) @ vector)) >= DUPLICATE_SIMILARITY:
            continue
        note = MemoryNote(
            contact_id=contact_id,
            content=text,
            origin=NoteOrigin.EXTRACTOR,
            source_message_ids=sorted(set(candidate.source_message_ids) & valid_ids),
            embedding=to_blob(vector),
            embedding_model=embedder.model_name,
        )
        session.add(note)
        existing.append(note)
        known.append(vector)
        known_texts.add(text.casefold())
        inserted += 1
    return inserted
