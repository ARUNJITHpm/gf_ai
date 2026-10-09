"""Suggestion linker job: match messages I sent to the suggestion they came from.

The phone can't reliably tell what I finally sent. So when a message of mine shows up
within 10 minutes after a suggestion and closely resembles one of its replies, the
suggestion log gets `final_text` (and `chosen_index` if the app didn't send it). Those
pairs teach the style refresh how I edit suggestions.
"""

from datetime import timedelta
from difflib import SequenceMatcher

from sqlalchemy import func, select

from app.jobs.base import JobContext, get_state
from app.models import Message, SuggestionLog
from app.models.enums import MessageKind, Sender

JOB = "linker"
WINDOW = timedelta(minutes=10)
MIN_SIMILARITY = 0.6


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.casefold().strip(), b.casefold().strip()).ratio()


def run(ctx: JobContext) -> str:
    with ctx.session_factory() as session:
        state = get_state(session, JOB)
        newest = session.scalar(select(func.max(Message.id))) or 0
        mine = session.scalars(
            select(Message)
            .where(
                Message.id > (state.last_message_id or 0),
                Message.sender == Sender.ME,
                Message.kind == MessageKind.TEXT,
                Message.linked_suggestion_id.is_(None),
            )
            .order_by(Message.id)
        ).all()

        linked = 0
        for message in mine:
            sent = message.sent_at or message.observed_at
            logs = session.scalars(
                select(SuggestionLog)
                .where(
                    SuggestionLog.contact_id == message.contact_id,
                    SuggestionLog.final_text.is_(None),
                    SuggestionLog.created_at >= sent - WINDOW,
                    SuggestionLog.created_at <= sent + timedelta(minutes=1),
                )
                .order_by(SuggestionLog.created_at.desc())
            ).all()
            for log in logs:
                texts = [str(s.get("text", "")) for s in log.suggestions]
                if log.chosen_index is not None and 0 <= log.chosen_index < len(texts):
                    candidates = [log.chosen_index]
                else:
                    candidates = list(range(len(texts)))
                scored = [(similarity(message.text, texts[i]), i) for i in candidates]
                if not scored:
                    continue
                score, index = max(scored)
                if score < MIN_SIMILARITY:
                    continue
                log.final_text = message.text
                if log.chosen_index is None:
                    log.chosen_index = index
                message.linked_suggestion_id = log.id
                linked += 1
                break

        state.last_message_id = newest
        session.commit()
        return f"linked {linked} of {len(mine)} sent messages"
