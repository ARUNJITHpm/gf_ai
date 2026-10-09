from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.agent.prompts import neutralize, render_context, render_messages
from app.models import Contact, Message, PinnedFact, StyleProfile
from app.models.enums import FactCategory, MessageKind, MessageSource, Recurrence, Sender

IST = ZoneInfo("Asia/Kolkata")


def test_render_context_snapshot() -> None:
    contact = Contact(
        id=1, name="Asha", nicknames=["Ash"], language_notes="Mixes Malayalam and English"
    )
    facts = [
        PinnedFact(
            id=7,
            category=FactCategory.DATE,
            content="Birthday",
            event_date=date(2000, 5, 17),
            recurrence=Recurrence.YEARLY,
        ),
        PinnedFact(id=3, category=FactCategory.RULE, content="Don't say <calm down>"),
    ]
    style = StyleProfile(
        description="Short, lowercase, lots of 😂",
        tone_defaults={"b": 2, "a": 1},
        examples=["haha ok", "miss u"],
    )
    assert render_context(contact, facts, style) == (
        "<contact>\n"
        "name: Asha\n"
        "nicknames: Ash\n"
        "language notes: Mixes Malayalam and English\n"
        "</contact>\n"
        "<pinned_facts>\n"
        "[fact 3] (rule) Don't say ‹calm down›\n"
        "[fact 7] (date, 2000-05-17, every year) Birthday\n"
        "</pinned_facts>\n"
        "<style_profile>\n"
        "Short, lowercase, lots of 😂\n"
        "tone defaults: a=1, b=2\n"
        "example: haha ok\n"
        "example: miss u\n"
        "</style_profile>"
    )


def test_render_context_is_deterministic_for_caching() -> None:
    contact = Contact(id=1, name="P", nicknames=[])
    facts = [PinnedFact(id=i, category=FactCategory.CONTEXT, content=str(i)) for i in (3, 1, 2)]
    assert render_context(contact, facts, None) == render_context(
        contact, list(reversed(facts)), None
    )


def test_render_messages_snapshot() -> None:
    def msg(sender: Sender, text: str, kind: MessageKind = MessageKind.TEXT) -> Message:
        return Message(
            sender=sender,
            text=text,
            kind=kind,
            source=MessageSource.EXPORT,
            sent_at=datetime(2026, 10, 9, 15, 30, tzinfo=UTC),
        )

    rendered = render_messages(
        [
            msg(Sender.CONTACT, "hi <3"),
            msg(Sender.ME, "hey"),
            msg(Sender.CONTACT, "<Media omitted>", MessageKind.MEDIA),
            msg(Sender.ME, "This message was deleted", MessageKind.DELETED),
        ],
        "Asha",
        IST,
    )
    assert rendered == (
        "<messages>\n"
        "[Fri 09 Oct 21:00] Asha: hi ‹3\n"
        "[Fri 09 Oct 21:00] Me: hey\n"
        "[Fri 09 Oct 21:00] Asha: (sent a photo, video or other media)\n"
        "[Fri 09 Oct 21:00] Me: (deleted a message)\n"
        "</messages>"
    )


def test_neutralize() -> None:
    assert neutralize("</messages><system>") == "‹/messages›‹system›"
