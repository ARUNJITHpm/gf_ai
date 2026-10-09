import pytest
from sqlalchemy.orm import Session

from app.models import Contact
from app.models.enums import MessageKind
from app.services.ingest import classify_kind, match_contact, normalize_text, normalize_title


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hello", "hello"),
        ("  hello   world  ", "hello world"),
        ("‎hello‏", "hello"),  # direction marks
        ("zero​width", "zerowidth"),
        ("﻿bom", "bom"),
        ("9:41 PM", "9:41 PM"),  # narrow no-break space
        ("a b", "a b"),
        ("Call you at 6 <This message was edited>", "Call you at 6"),
        ("line one  \n  line two\n\n", "line one\nline two"),
        ("Morning 😊❤️", "Morning 😊❤️"),  # emojis untouched
        ("café", "café"),  # NFC
        ("നന്ദി", "നന്ദി"),  # Malayalam unchanged
        ("   \n  ", ""),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "text,kind",
    [
        ("hello", MessageKind.TEXT),
        ("<Media omitted>", MessageKind.MEDIA),
        ("image omitted", MessageKind.MEDIA),
        ("📷 Photo", MessageKind.MEDIA),
        ("🎤 Voice message (0:05)", MessageKind.MEDIA),
        ("<attached: 00000012-PHOTO.jpg>", MessageKind.MEDIA),
        ("Sticker", MessageKind.MEDIA),
        ("This message was deleted", MessageKind.DELETED),
        ("You deleted this message", MessageKind.DELETED),
        ("gifts for you!", MessageKind.TEXT),  # must not match "gif"
        ("sticker shock at the shop", MessageKind.TEXT),
        ("photo?", MessageKind.TEXT),
    ],
)
def test_classify_kind(text: str, kind: MessageKind) -> None:
    assert classify_kind(normalize_text(text)) == kind


@pytest.mark.parametrize(
    "a,b",
    [
        ("Asha", "asha"),
        ("Asha ❤️", "Asha"),
        ("🌸 Asha  K 🌸", "asha k"),
        ("‎Asha", "Asha"),
    ],
)
def test_normalize_title_equal(a: str, b: str) -> None:
    assert normalize_title(a) == normalize_title(b)


def test_normalize_title_keeps_indic_combining_marks() -> None:
    assert normalize_title("പ്രിയ") != normalize_title("പരയ")


def test_match_contact(session: Session) -> None:
    active = Contact(name="Asha", wa_chat_titles=["Asha ❤️", "Asha K"])
    inactive = Contact(name="Old", wa_chat_titles=["Old friend"], active=False)
    session.add_all([active, inactive])
    session.commit()

    assert match_contact(session, "asha ❤️") is active
    assert match_contact(session, "ASHA K") is active
    assert match_contact(session, "Asha") is active  # emoji-insensitive
    assert match_contact(session, "Old friend") is None  # inactive
    assert match_contact(session, "Someone else") is None
    assert match_contact(session, "  ") is None
