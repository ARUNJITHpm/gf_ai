"""Which approved notes go into the prompt (spec §8, node 2).

The last few messages from the contact are embedded and compared with every approved note
for that contact; the top-k above a minimum score are returned. Only `approved` notes are
ever returned. Notes without an up-to-date embedding are embedded here, on first use, so
approving a note in the admin never waits for the model.
"""

from collections.abc import Sequence

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.memory.embeddings import Embedder, from_blob, to_blob
from app.models import MemoryNote, Message
from app.models.enums import MessageKind, NoteStatus, Sender

QUERY_MESSAGES = 3


def ensure_embeddings(notes: Sequence[MemoryNote], embedder: Embedder) -> int:
    """Embed notes whose vector is missing or from another model. Caller commits."""
    stale = [n for n in notes if n.embedding is None or n.embedding_model != embedder.model_name]
    if stale:
        vectors = embedder.embed_passages([n.content for n in stale])
        for note, vector in zip(stale, vectors, strict=True):
            note.embedding = to_blob(vector)
            note.embedding_model = embedder.model_name
    return len(stale)


def retrieve_notes(
    session: Session,
    contact_id: int,
    recent_messages: Sequence[Message],
    *,
    top_k: int,
    embedder: Embedder | None = None,
    min_score: float = 0.0,
) -> list[MemoryNote]:
    if top_k <= 0:
        return []
    notes = list(
        session.scalars(
            select(MemoryNote)
            .where(MemoryNote.contact_id == contact_id, MemoryNote.status == NoteStatus.APPROVED)
            .order_by(MemoryNote.updated_at.desc(), MemoryNote.id.desc())
        )
    )
    query_texts = [
        m.text for m in recent_messages if m.sender == Sender.CONTACT and m.kind == MessageKind.TEXT
    ][-QUERY_MESSAGES:]
    if embedder is None or not notes or not query_texts:
        return notes[:top_k]  # nothing to compare with: most recently updated first

    ensure_embeddings(notes, embedder)
    query = embedder.embed_queries([" ".join(query_texts)])[0]
    matrix = np.stack([from_blob(n.embedding) for n in notes if n.embedding is not None])
    scores = matrix @ query
    ranked = sorted(zip(scores.tolist(), range(len(notes)), strict=True), reverse=True)
    return [notes[i] for score, i in ranked[:top_k] if score >= min_score]
