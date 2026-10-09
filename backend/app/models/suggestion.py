from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.contact import Contact


class SuggestionLog(TimestampMixin, Base):
    """One row per /suggest call: what was suggested, what I picked, and what it cost."""

    __tablename__ = "suggestion_log"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    contact: Mapped[Contact] = relationship()
    context_hash: Mapped[str] = mapped_column(String(64), index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # ids only, no text
    suggestions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    tone: Mapped[str | None] = mapped_column(String(50))
    chosen_index: Mapped[int | None]
    final_text: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
