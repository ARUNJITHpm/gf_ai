"""Summarizer job: roll chat older than 30 days into short *pending* summary notes.

Works week by week (local time). A message is processed once when it first becomes old
enough. Two cursors make that hold even for imported history: `cursor_at` (the age cut-off
of the previous run) and `last_message_id` (the newest message id at that run).
"""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, or_, select

from app.agent import prompts
from app.agent.schemas import ChatSummary
from app.jobs.base import JobContext, by_contact, chunked, get_state
from app.models import Contact, MemoryNote, Message
from app.models.enums import MessageKind, NoteOrigin

JOB = "summarizer"
AGE = timedelta(days=30)
MIN_MESSAGES = 10  # weeks quieter than this aren't worth a note
CHUNK = 300


def run(ctx: JobContext) -> str:
    cutoff = ctx.now - AGE
    with ctx.session_factory() as session:
        state = get_state(session, JOB)
        newest = session.scalar(select(func.max(Message.id))) or 0
        when = func.coalesce(Message.sent_at, Message.observed_at)
        conditions = [when < cutoff, Message.kind == MessageKind.TEXT]
        if state.cursor_at is not None:
            # Newly old since last run, or inserted since last run (e.g. a late import).
            conditions.append(
                or_(when >= state.cursor_at, Message.id > (state.last_message_id or 0))
            )
        old = session.scalars(select(Message).where(*conditions).order_by(when, Message.id)).all()

        created = 0
        for contact_id, messages in by_contact(old).items():
            contact = session.get(Contact, contact_id)
            if contact is None:
                continue
            weeks: dict[tuple[int, int], list[Message]] = defaultdict(list)
            for m in messages:
                year, week, _ = (
                    (m.sent_at or m.observed_at).astimezone(ctx.settings.tz).isocalendar()
                )
                weeks[(year, week)].append(m)
            for week_messages in weeks.values():
                if len(week_messages) < MIN_MESSAGES:
                    continue
                for chunk in chunked(week_messages, CHUNK):
                    result = ctx.llm.complete_json(
                        model=ctx.settings.llm_fast_model,
                        system=[prompts.SUMMARY_SYSTEM],
                        prompt=prompts.render_summary_prompt(
                            contact=contact, messages=chunk, tz=ctx.settings.tz
                        ),
                        schema=ChatSummary,
                        effort="low",
                        max_tokens=2000,
                    )
                    summary = result.parsed
                    if summary is None or not summary.worth_keeping or not summary.summary.strip():
                        continue
                    start = (chunk[0].sent_at or chunk[0].observed_at).astimezone(ctx.settings.tz)
                    session.add(
                        MemoryNote(
                            contact_id=contact_id,
                            content=f"Week of {start:%d %b %Y}: {summary.summary.strip()}",
                            origin=NoteOrigin.SUMMARIZER,
                            source_message_ids=[m.id for m in chunk],
                        )
                    )
                    created += 1

        state.cursor_at = cutoff
        state.last_message_id = newest
        session.commit()
        return f"created {created} summary notes from {len(old)} old messages"
