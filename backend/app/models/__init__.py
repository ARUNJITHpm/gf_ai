"""All models, imported here so `Base.metadata` is complete for Alembic and tests."""

from app.models.base import Base
from app.models.contact import Contact
from app.models.memory import MemoryNote, PinnedFact, StyleProfile
from app.models.message import Message
from app.models.reminder import Reminder
from app.models.suggestion import SuggestionLog
from app.models.system import JobState, Setting

__all__ = [
    "Base",
    "Contact",
    "JobState",
    "MemoryNote",
    "Message",
    "PinnedFact",
    "Reminder",
    "Setting",
    "StyleProfile",
    "SuggestionLog",
]
