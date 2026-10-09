"""What the assistant knows: pinned facts, reviewed notes, and my writing style."""

from datetime import date
from typing import Any

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum
from app.models.contact import Contact
from app.models.enums import FactCategory, NoteOrigin, NoteStatus, Recurrence, StyleStatus


class PinnedFact(TimestampMixin, Base):
    """Hand-written facts. Always sent to the model; `rule` facts are hard constraints."""

    __tablename__ = "pinned_fact"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    contact: Mapped[Contact] = relationship()
    category: Mapped[FactCategory] = mapped_column(str_enum(FactCategory))
    content: Mapped[str] = mapped_column(Text)
    event_date: Mapped[date | None]
    recurrence: Mapped[Recurrence] = mapped_column(str_enum(Recurrence), default=Recurrence.NONE)
    active: Mapped[bool] = mapped_column(default=True)


class MemoryNote(TimestampMixin, Base):
    """Auto-extracted notes. Only `approved` ones reach the model."""

    __tablename__ = "memory_note"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    contact: Mapped[Contact] = relationship()
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)  # float32 vector
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[NoteStatus] = mapped_column(
        str_enum(NoteStatus), default=NoteStatus.PENDING, index=True
    )
    origin: Mapped[NoteOrigin] = mapped_column(str_enum(NoteOrigin))
    source_message_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)


class StyleProfile(TimestampMixin, Base):
    """How I write to this contact. Exactly one `active` per contact."""

    __tablename__ = "style_profile"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    contact: Mapped[Contact] = relationship()
    description: Mapped[str] = mapped_column(Text)
    tone_defaults: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    examples: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[StyleStatus] = mapped_column(str_enum(StyleStatus), default=StyleStatus.PENDING)
    version: Mapped[int] = mapped_column(default=1)
