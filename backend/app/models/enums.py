"""String enums used by the models (stored as their values)."""

from enum import StrEnum


class Sender(StrEnum):
    ME = "me"
    CONTACT = "contact"


class MessageSource(StrEnum):
    EXPORT = "export"
    NOTIFICATION = "notification"
    SCREEN = "screen"
    MANUAL = "manual"


class MessageKind(StrEnum):
    TEXT = "text"
    MEDIA = "media"
    DELETED = "deleted"
    SYSTEM = "system"


class FactCategory(StrEnum):
    PROFILE = "profile"
    DATE = "date"
    RULE = "rule"
    INSIDE_JOKE = "inside_joke"
    CONTEXT = "context"


class Recurrence(StrEnum):
    NONE = "none"
    YEARLY = "yearly"


class NoteStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class NoteOrigin(StrEnum):
    EXTRACTOR = "extractor"
    SUMMARIZER = "summarizer"
    MANUAL = "manual"


class StyleStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ReminderOrigin(StrEnum):
    PROMISE = "promise"
    DATE = "date"
    MANUAL = "manual"
