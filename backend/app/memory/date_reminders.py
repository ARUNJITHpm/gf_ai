"""Date reminders job: reminders 7 days and 1 day before pinned dates (no LLM)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.jobs.base import JobContext
from app.models import PinnedFact, Reminder
from app.models.enums import FactCategory, Recurrence, ReminderOrigin

JOB = "date_reminders"
OFFSETS_DAYS = (7, 1)
REMIND_AT = time(9, 0)  # local time on the reminder day


def next_occurrence(event: date, recurrence: Recurrence, today: date) -> date | None:
    if recurrence == Recurrence.NONE:
        return event if event >= today else None
    for year in (today.year, today.year + 1):
        day = event.day
        if event.month == 2 and event.day == 29 and not _is_leap(year):
            day = 28
        occurrence = date(year, event.month, day)
        if occurrence >= today:
            return occurrence
    return None


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def run(ctx: JobContext) -> str:
    tz: ZoneInfo = ctx.settings.tz
    today = ctx.now.astimezone(tz).date()
    created = 0
    with ctx.session_factory() as session:
        facts = session.scalars(
            select(PinnedFact).where(
                PinnedFact.active,
                PinnedFact.category == FactCategory.DATE,
                PinnedFact.event_date.is_not(None),
            )
        ).all()
        for fact in facts:
            assert fact.event_date is not None
            occurrence = next_occurrence(fact.event_date, fact.recurrence, today)
            if occurrence is None:
                continue
            for days in OFFSETS_DAYS:
                due = datetime.combine(occurrence - timedelta(days=days), REMIND_AT, tzinfo=tz)
                if due < ctx.now:
                    continue
                key = f"date:{fact.id}:{occurrence.year}:{days}"
                if session.scalar(select(Reminder.id).where(Reminder.origin_key == key)):
                    continue
                when = "tomorrow" if days == 1 else f"in {days} days"
                session.add(
                    Reminder(
                        contact_id=fact.contact_id,
                        title=f"{fact.content} {when} ({occurrence:%d %b})"[:500],
                        due_at=due,
                        origin=ReminderOrigin.DATE,
                        origin_key=key,
                    )
                )
                created += 1
        session.commit()
    return f"created {created} date reminders"
