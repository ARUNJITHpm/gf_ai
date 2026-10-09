"""Runtime state: background job watermarks and editable settings."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class JobState(TimestampMixin, Base):
    __tablename__ = "job_state"

    job_name: Mapped[str] = mapped_column(String(100), unique=True)
    last_message_id: Mapped[int | None]  # watermark: highest message id processed
    cursor_at: Mapped[datetime | None]  # time watermark, for jobs that work by message age
    last_run_at: Mapped[datetime | None]
    last_status: Mapped[str | None] = mapped_column(String(200))


class Setting(TimestampMixin, Base):
    """Key/value settings editable in the admin (paused, default_tone, recent_messages_n, ...)."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(100), unique=True)
    value: Mapped[Any] = mapped_column(JSON)
