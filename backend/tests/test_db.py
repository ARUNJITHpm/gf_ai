from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.models import Contact, Message, PinnedFact
from app.models.enums import FactCategory, MessageSource, Sender


def make_contact(session: Session) -> Contact:
    contact = Contact(name="Test", wa_chat_titles=["Test"])
    session.add(contact)
    session.commit()
    return contact


def make_message(contact_id: int, **overrides: object) -> Message:
    fields: dict[str, object] = {
        "contact_id": contact_id,
        "sender": Sender.CONTACT,
        "text": "hi",
        "source": MessageSource.MANUAL,
        "dedupe_hash": "0" * 64,
    }
    fields.update(overrides)
    return Message(**fields)


def test_pragmas(session: Session) -> None:
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_foreign_keys_enforced(session: Session) -> None:
    session.add(make_message(contact_id=999))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_contact_cascades(session: Session) -> None:
    contact = make_contact(session)
    session.add_all(
        [
            make_message(contact.id),
            PinnedFact(contact_id=contact.id, category=FactCategory.RULE, content="x"),
        ]
    )
    session.commit()

    session.execute(text("DELETE FROM contact WHERE id = :id"), {"id": contact.id})
    session.commit()
    assert session.scalar(select(Message)) is None
    assert session.scalar(select(PinnedFact)) is None


def test_datetimes_round_trip_as_utc(session: Session) -> None:
    contact = make_contact(session)
    ist = timezone(timedelta(hours=5, minutes=30))
    session.add(make_message(contact.id, sent_at=datetime(2026, 1, 1, 10, 0, tzinfo=ist)))
    session.commit()
    session.expunge_all()

    message = session.scalars(select(Message)).one()
    assert message.sent_at == datetime(2026, 1, 1, 4, 30, tzinfo=UTC)
    assert message.sent_at.tzinfo is UTC
    assert message.created_at.tzinfo is UTC


def test_naive_datetime_rejected(session: Session) -> None:
    contact = make_contact(session)
    session.add(make_message(contact.id, sent_at=datetime(2026, 1, 1, 10, 0)))
    with pytest.raises(StatementError, match="naive datetime"):
        session.commit()


def test_enum_stored_as_value_and_checked(session: Session) -> None:
    contact = make_contact(session)
    session.add(make_message(contact.id))
    session.commit()
    assert session.execute(text("SELECT sender FROM message")).scalar() == "contact"

    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE message SET sender = 'her'"))


def test_sql_errors_do_not_include_message_text(session: Session) -> None:
    session.add(make_message(contact_id=999, text="very private words"))
    with pytest.raises(IntegrityError) as excinfo:
        session.commit()
    assert "very private words" not in str(excinfo.value)
