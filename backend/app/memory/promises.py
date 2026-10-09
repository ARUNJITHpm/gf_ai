"""Promise tracker job: turn my commitments ("I'll call you tonight") into reminders."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.agent import prompts
from app.agent.schemas import Promises
from app.jobs.base import JobContext, by_contact, chunked, get_state
from app.models import Contact, Message, Reminder
from app.models.enums import MessageKind, ReminderOrigin, Sender

JOB = "promises"
CHUNK = 80
DEFAULT_DELAY = timedelta(hours=3)  # when a promise has no usable time attached
# Only recent promises matter. This also keeps an imported chat history from turning into
# hundreds of long-overdue reminders.
MAX_PROMISE_AGE = timedelta(days=3)


def run(ctx: JobContext) -> str:
    tz = ctx.settings.tz
    with ctx.session_factory() as session:
        state = get_state(session, JOB)
        watermark = state.last_message_id or 0
        newest = session.scalar(select(func.max(Message.id))) or 0
        new = session.scalars(
            select(Message)
            .where(Message.id > watermark, Message.kind == MessageKind.TEXT)
            .order_by(Message.id)
        ).all()

        created = 0
        for contact_id, messages in by_contact(new).items():
            recent_cutoff = ctx.now - MAX_PROMISE_AGE
            messages = [m for m in messages if (m.sent_at or m.observed_at) >= recent_cutoff]
            if not any(m.sender == Sender.ME for m in messages):
                continue  # nothing recent I said, nothing I promised
            contact = session.get(Contact, contact_id)
            if contact is None:
                continue
            for chunk in chunked(messages, CHUNK):
                result = ctx.llm.complete_json(
                    model=ctx.settings.llm_fast_model,
                    system=[prompts.PROMISE_SYSTEM],
                    prompt=prompts.render_promise_prompt(
                        contact=contact, messages=chunk, now=ctx.now, tz=tz
                    ),
                    schema=Promises,
                    effort="low",
                    max_tokens=4000,
                )
                if result.parsed is None:
                    continue
                mine = {m.id: m for m in chunk if m.sender == Sender.ME}
                seen: dict[int, int] = {}
                for item in result.parsed.items:
                    message = mine.get(item.message_id)
                    if message is None or not item.title.strip():
                        continue  # not my message, or not in this chunk
                    sent = message.sent_at or message.observed_at
                    due = _parse_due(item.due_local, tz)
                    if due is None or due < sent:
                        due = sent + DEFAULT_DELAY  # no time, or a time before the promise
                    n = seen[message.id] = seen.get(message.id, -1) + 1
                    key = f"promise:{message.id}:{n}"
                    if session.scalar(select(Reminder.id).where(Reminder.origin_key == key)):
                        continue
                    session.add(
                        Reminder(
                            contact_id=contact_id,
                            title=item.title.strip()[:500],
                            due_at=due,
                            origin=ReminderOrigin.PROMISE,
                            origin_key=key,
                        )
                    )
                    created += 1

        state.last_message_id = newest
        session.commit()
        return f"created {created} reminders from {len(new)} messages"


def _parse_due(value: str, tz: ZoneInfo) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        local = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=tz)
