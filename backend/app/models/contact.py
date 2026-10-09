from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Contact(TimestampMixin, Base):
    __tablename__ = "contact"

    name: Mapped[str] = mapped_column(String(200))
    # Chat titles as WhatsApp shows them; a list because a rename changes the title.
    wa_chat_titles: Mapped[list[Any]] = mapped_column(JSON, default=list)
    nicknames: Mapped[list[Any]] = mapped_column(JSON, default=list)
    language_notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)

    def __str__(self) -> str:
        return self.name
