"""Logging setup and a guard against message text ending up in logs (spec §12).

Use `log_event("ingest.done", contact_id=1, inserted=3)` instead of free-form log lines
anywhere message data is involved.
"""

import logging

logger = logging.getLogger("app")

# Field names that tend to carry message content. Never log these.
FORBIDDEN_FIELDS = frozenset(
    {"text", "content", "message", "messages", "body", "final_text", "draft_text", "prompt"}
)
# Short strings (ids, names of jobs, statuses) are fine; anything longer looks like content.
MAX_STR_LEN = 64

LogValue = int | float | bool | str | None


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.setLevel(level)


def log_event(event: str, level: int = logging.INFO, **fields: LogValue) -> None:
    for key, value in fields.items():
        if key in FORBIDDEN_FIELDS:
            raise ValueError(f"log_event: field {key!r} may contain message text")
        if not isinstance(value, int | float | bool | str | None):
            raise TypeError(f"log_event: field {key!r} must be a scalar")
        if isinstance(value, str) and len(value) > MAX_STR_LEN:
            raise ValueError(f"log_event: field {key!r} is too long to be an id or status")
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.log(level, "%s %s", event, rendered)
