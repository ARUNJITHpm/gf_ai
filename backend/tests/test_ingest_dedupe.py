from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, Message
from app.models.enums import MessageKind, MessageSource, Sender
from app.services.ingest import IncomingMessage, ingest_messages

T0 = datetime(2026, 3, 13, 10, 0, tzinfo=UTC)
ME, THEM = Sender.ME, Sender.CONTACT
EXPORT, NOTIF, SCREEN = MessageSource.EXPORT, MessageSource.NOTIFICATION, MessageSource.SCREEN


@pytest.fixture
def contact(session: Session) -> Contact:
    contact = Contact(name="Sam", wa_chat_titles=["Sam"])
    session.add(contact)
    session.commit()
    return contact


def msg(
    sender: Sender, text: str, source: MessageSource, at: datetime | None = None
) -> IncomingMessage:
    return IncomingMessage(sender=sender, text=text, source=source, sent_at=at)


def stored(session: Session) -> list[tuple[str, str, datetime | None]]:
    rows = session.scalars(select(Message).order_by(Message.id)).all()
    return [(row.sender.value, row.text, row.sent_at) for row in rows]


def test_same_message_twice_is_stored_once(session: Session, contact: Contact) -> None:
    first = ingest_messages(session, contact, [msg(THEM, "hi", NOTIF, T0)])
    second = ingest_messages(session, contact, [msg(THEM, "hi", NOTIF, T0)])
    assert (first.inserted, second.inserted, second.skipped) == (1, 0, 1)
    assert len(stored(session)) == 1


def test_same_message_from_two_sources_a_minute_apart(session: Session, contact: Contact) -> None:
    ingest_messages(session, contact, [msg(THEM, "hi there", NOTIF, T0 + timedelta(seconds=50))])
    result = ingest_messages(session, contact, [msg(THEM, "hi there", EXPORT, T0)])
    assert (result.inserted, result.skipped) == (0, 1)


def test_text_normalization_applies_before_dedupe(session: Session, contact: Contact) -> None:
    ingest_messages(session, contact, [msg(THEM, "Call at 6", NOTIF, T0)])
    result = ingest_messages(
        session, contact, [msg(THEM, "‎Call  at 6 <This message was edited>", EXPORT, T0)]
    )
    assert result.inserted == 0


def test_same_text_an_hour_apart_is_two_messages(session: Session, contact: Contact) -> None:
    ingest_messages(session, contact, [msg(ME, "ok", NOTIF, T0)])
    result = ingest_messages(session, contact, [msg(ME, "ok", NOTIF, T0 + timedelta(hours=1))])
    assert result.inserted == 1
    assert len(stored(session)) == 2


def test_repeated_text_in_same_minute_kept_and_reimport_is_idempotent(
    session: Session, contact: Contact
) -> None:
    batch = [msg(THEM, "ok", EXPORT, T0), msg(THEM, "ok", EXPORT, T0)]
    assert ingest_messages(session, contact, batch).inserted == 2
    again = ingest_messages(session, contact, batch)
    assert (again.inserted, again.skipped) == (0, 2)


def test_sender_is_part_of_identity(session: Session, contact: Contact) -> None:
    result = ingest_messages(
        session, contact, [msg(ME, "ok", NOTIF, T0), msg(THEM, "ok", NOTIF, T0)]
    )
    assert result.inserted == 2


def test_screen_then_timestamp_fills_sent_at(session: Session, contact: Contact) -> None:
    ingest_messages(session, contact, [msg(THEM, "see you", SCREEN)], now=T0)
    result = ingest_messages(
        session, contact, [msg(THEM, "see you", NOTIF, T0 - timedelta(minutes=3))], now=T0
    )
    assert (result.inserted, result.updated) == (0, 1)
    assert stored(session) == [("contact", "see you", T0 - timedelta(minutes=3))]


def test_screen_batch_overlapping_tail_inserts_only_new_suffix(
    session: Session, contact: Contact
) -> None:
    ingest_messages(
        session,
        contact,
        [
            msg(THEM, "a", NOTIF, T0),
            msg(ME, "b", NOTIF, T0 + timedelta(minutes=1)),
            msg(THEM, "c", NOTIF, T0 + timedelta(minutes=2)),
        ],
    )
    screen = [
        msg(ME, "b", SCREEN),
        msg(THEM, "c", SCREEN),
        msg(ME, "d", SCREEN),
        msg(THEM, "e", SCREEN),
    ]
    result = ingest_messages(session, contact, screen, now=T0 + timedelta(minutes=5))
    assert (result.inserted, result.skipped) == (2, 2)
    assert [text for _, text, _ in stored(session)] == ["a", "b", "c", "d", "e"]


def test_screen_batch_reread_after_scrolling_inserts_nothing(
    session: Session, contact: Contact
) -> None:
    screen = [msg(THEM, "x", SCREEN), msg(ME, "y", SCREEN), msg(THEM, "z", SCREEN)]
    ingest_messages(session, contact, screen, now=T0)
    # Same window seen again, and a window shifted up by one (scrolled).
    assert ingest_messages(session, contact, screen, now=T0 + timedelta(seconds=5)).inserted == 0
    assert (
        ingest_messages(session, contact, screen[:2], now=T0 + timedelta(seconds=9)).inserted == 0
    )
    assert len(stored(session)) == 3


def test_screen_repeated_ok_aligned_correctly(session: Session, contact: Contact) -> None:
    """A new "ok" after an old "ok" is kept because alignment, not text lookup, decides."""
    ingest_messages(session, contact, [msg(ME, "ok", SCREEN), msg(THEM, "sure", SCREEN)], now=T0)
    later = [msg(ME, "ok", SCREEN), msg(THEM, "sure", SCREEN), msg(ME, "ok", SCREEN)]
    result = ingest_messages(session, contact, later, now=T0 + timedelta(minutes=1))
    assert result.inserted == 1
    assert [text for _, text, _ in stored(session)] == ["ok", "sure", "ok"]


def test_screen_far_scrollback_does_not_duplicate_history(
    session: Session, contact: Contact
) -> None:
    history = [
        msg(THEM if i % 2 else ME, f"old message {i}", EXPORT, T0 + timedelta(minutes=i))
        for i in range(150)
    ]
    ingest_messages(session, contact, history)
    # Window from far back (older than the 100-message tail used for alignment).
    window = [msg(ME, "old message 10", SCREEN), msg(THEM, "old message 11", SCREEN)]
    result = ingest_messages(session, contact, window, now=T0 + timedelta(days=1))
    assert result.inserted == 0


def test_kinds_and_empty_text(session: Session, contact: Contact) -> None:
    result = ingest_messages(
        session,
        contact,
        [
            msg(THEM, "<Media omitted>", EXPORT, T0),
            msg(THEM, "This message was deleted", EXPORT, T0),
            msg(THEM, "‎  ", EXPORT, T0),
        ],
    )
    assert (result.inserted, result.skipped) == (2, 1)
    kinds = session.scalars(select(Message.kind).order_by(Message.id)).all()
    assert kinds == [MessageKind.MEDIA, MessageKind.DELETED]
