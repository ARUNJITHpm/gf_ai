from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, str_enum
from app.models.enums import ReminderOrigin


class Reminder(TimestampMixin, Base):
    __tablename__ = "reminder"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contact.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    due_at: Mapped[datetime] = mapped_column(index=True)
    origin: Mapped[ReminderOrigin] = mapped_column(str_enum(ReminderOrigin))
    # e.g. "promise:<message_id>" or "date:<fact_id>:<year>:<offset>"; makes jobs idempotent.
    origin_key: Mapped[str] = mapped_column(String(200), unique=True)
    done: Mapped[bool] = mapped_column(default=False)
    notified_at: Mapped[datetime | None]
