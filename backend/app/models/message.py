from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, str_enum, utcnow
from app.models.contact import Contact
from app.models.enums import MessageKind, MessageSource, Sender


class Message(TimestampMixin, Base):
    __tablename__ = "message"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    contact: Mapped[Contact] = relationship()
    sender: Mapped[Sender] = mapped_column(str_enum(Sender))
    text: Mapped[str] = mapped_column(Text)  # normalized
    # When it was sent (UTC). Null for screen reads where the time wasn't visible.
    sent_at: Mapped[datetime | None]
    # When the backend first saw it; used for ordering when sent_at is null.
    observed_at: Mapped[datetime] = mapped_column(default=utcnow)
    source: Mapped[MessageSource] = mapped_column(str_enum(MessageSource))
    kind: Mapped[MessageKind] = mapped_column(str_enum(MessageKind), default=MessageKind.TEXT)
    # sha256(contact_id|sender|normalized text). Not unique: dedupe also uses time (spec §6).
    dedupe_hash: Mapped[str] = mapped_column(String(64), index=True)
    linked_suggestion_id: Mapped[int | None] = mapped_column(
        ForeignKey("suggestion_log.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_message_contact_sent", "contact_id", "sent_at"),)
