"""Runtime settings stored in the `setting` table and editable in the admin.

Startup config (keys, model names, timezone) lives in `.env`; these are the knobs I may
want to flip while the app runs.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting


@dataclass(frozen=True)
class SettingSpec:
    default: Any
    type: type
    help: str


SETTINGS: dict[str, SettingSpec] = {
    "paused": SettingSpec(False, bool, "When true, /suggest returns no suggestions."),
    "default_tone": SettingSpec("warm", str, "Tone used when the app doesn't ask for one."),
    "recent_messages_n": SettingSpec(25, int, "How many recent messages the model sees."),
    "notes_top_k": SettingSpec(5, int, "How many approved notes are retrieved per request."),
    "notes_min_score": SettingSpec(
        0.2, float, "Minimum similarity (0-1) for a note to be used in a prompt."
    ),
    "suggest_cache_seconds": SettingSpec(60, int, "Reuse identical /suggest results this long."),
}


def validate_setting(key: str, value: Any) -> Any:
    """Return the value if valid for `key`, else raise ValueError with a readable message."""
    spec = SETTINGS.get(key)
    if spec is None:
        raise ValueError(f"unknown setting {key!r}; known: {sorted(SETTINGS)}")
    # bool is a subclass of int; don't let `true` pass as an int or 1 as a bool.
    if spec.type is float and type(value) is int:
        value = float(value)
    if type(value) is not spec.type:
        raise ValueError(f"{key} must be a {spec.type.__name__} (JSON), got {value!r}")
    if isinstance(value, int | float) and not isinstance(value, bool) and value < 0:
        raise ValueError(f"{key} must not be negative")
    return value


def get_setting(session: Session, key: str) -> Any:
    spec = SETTINGS[key]
    value = session.scalar(select(Setting.value).where(Setting.key == key))
    if value is None:
        return spec.default
    try:
        return validate_setting(key, value)
    except ValueError:
        return spec.default


def seed_defaults(session: Session) -> int:
    """Insert any missing settings with their defaults. Returns how many were added."""
    existing = set(session.scalars(select(Setting.key)))
    missing = [Setting(key=k, value=s.default) for k, s in SETTINGS.items() if k not in existing]
    session.add_all(missing)
    session.commit()
    return len(missing)
