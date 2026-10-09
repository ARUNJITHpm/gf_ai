"""Style refresh job: draft a new *pending* style profile from how I actually write.

Runs weekly. A contact gets a new draft once 20+ new messages of mine have arrived since
its latest profile. Input: my recent messages plus the edits I made to suggestions
(suggested text vs. what I sent). The admin activates a draft; nothing changes until then.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import prompts
from app.agent.schemas import StyleDraft
from app.jobs.base import JobContext
from app.models import Contact, Message, StyleProfile, SuggestionLog
from app.models.enums import MessageKind, Sender, StyleStatus

JOB = "style"
MIN_NEW_MESSAGES = 20
RECENT_MESSAGES = 200
MAX_CORRECTIONS = 20
MAX_EXAMPLES = 6


def run(ctx: JobContext) -> str:
    created = 0
    with ctx.session_factory() as session:
        for contact in session.scalars(select(Contact).where(Contact.active)).all():
            latest = session.scalar(
                select(func.max(StyleProfile.created_at)).where(
                    StyleProfile.contact_id == contact.id
                )
            )
            mine = [
                Message.contact_id == contact.id,
                Message.sender == Sender.ME,
                Message.kind == MessageKind.TEXT,
            ]
            new_count = session.scalar(
                select(func.count(Message.id)).where(
                    *mine, *([Message.observed_at > latest] if latest else [])
                )
            )
            if (new_count or 0) < MIN_NEW_MESSAGES:
                continue

            when = func.coalesce(Message.sent_at, Message.observed_at)
            texts = list(
                reversed(
                    session.scalars(
                        select(Message.text)
                        .where(*mine)
                        .order_by(when.desc())
                        .limit(RECENT_MESSAGES)
                    ).all()
                )
            )
            corrections = _corrections(session, contact.id)
            result = ctx.llm.complete_json(
                model=ctx.settings.llm_model,  # quality matters more than speed here
                system=[prompts.STYLE_SYSTEM],
                prompt=prompts.render_style_prompt(my_messages=texts, corrections=corrections),
                schema=StyleDraft,
                effort="medium",
                max_tokens=4000,
            )
            draft = result.parsed
            if draft is None or not draft.description.strip():
                continue
            examples = [texts[i] for i in draft.example_indexes if 0 <= i < len(texts)]
            version = session.scalar(
                select(func.max(StyleProfile.version)).where(StyleProfile.contact_id == contact.id)
            )
            session.add(
                StyleProfile(
                    contact_id=contact.id,
                    description=draft.description.strip(),
                    tone_defaults={"default": draft.default_tone.strip()},
                    examples=list(dict.fromkeys(examples))[:MAX_EXAMPLES],
                    status=StyleStatus.PENDING,
                    version=(version or 0) + 1,
                )
            )
            session.commit()
            created += 1
    return f"drafted {created} style profiles"


def _corrections(session: Session, contact_id: int) -> list[tuple[str, str]]:
    """(suggested, sent) pairs where I changed the suggestion before sending."""
    logs = session.scalars(
        select(SuggestionLog)
        .where(
            SuggestionLog.contact_id == contact_id,
            SuggestionLog.final_text.is_not(None),
            SuggestionLog.chosen_index.is_not(None),
        )
        .order_by(SuggestionLog.id.desc())
        .limit(MAX_CORRECTIONS * 3)
    ).all()
    pairs: list[tuple[str, str]] = []
    for log in logs:
        index = log.chosen_index
        if index is None or not 0 <= index < len(log.suggestions) or log.final_text is None:
            continue
        suggested = str(log.suggestions[index].get("text", ""))
        if suggested.strip() != log.final_text.strip():
            pairs.append((suggested, log.final_text))
        if len(pairs) >= MAX_CORRECTIONS:
            break
    return list(reversed(pairs))
