import io
import logging
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Contact, Message

FIXTURES = Path(__file__).parent / "fixtures" / "exports"
DMY = (FIXTURES / "android_dmy_24h.txt").read_bytes()


@pytest.fixture
def contact(session: Session) -> Contact:
    contact = Contact(name="Sam", wa_chat_titles=["Sam 🌸"])
    session.add(contact)
    session.commit()
    return contact


def count_messages(session: Session) -> int:
    session.expire_all()
    return session.scalar(select(func.count(Message.id))) or 0


def post_import(
    client: TestClient,
    headers: dict[str, str],
    contact_id: int,
    filename: str = "WhatsApp Chat with Sam.txt",
    data: bytes = DMY,
    **form: str,
) -> object:
    fields = {
        "contact_id": str(contact_id),
        "my_author_name": "Alex",
        "contact_author_name": "Sam",
        **form,
    }
    return client.post(
        "/import/whatsapp-export", headers=headers, data=fields, files={"file": (filename, data)}
    )


# --- /ingest ----------------------------------------------------------------------------


def test_ingest_counts_and_dedupe(
    client: TestClient, auth_headers: dict[str, str], contact: Contact, session: Session
) -> None:
    body = {
        "contact_hint": "sam",
        "messages": [
            {
                "sender": "contact",
                "text": "hi",
                "sent_at": "2026-10-09T21:54:01+05:30",
                "source": "notification",
            },
            {"sender": "me", "text": "hey", "source": "screen"},
        ],
    }
    first = client.post("/ingest", json=body, headers=auth_headers)
    assert first.status_code == 200
    assert first.json() == {"contact_id": contact.id, "inserted": 2, "skipped": 0, "updated": 0}

    second = client.post("/ingest", json=body, headers=auth_headers)
    assert second.json()["inserted"] == 0
    assert count_messages(session) == 2


def test_ingest_unknown_contact_is_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = {"contact_hint": "Nobody", "messages": []}
    response = client.post("/ingest", json=body, headers=auth_headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown contact"}


def test_ingest_requires_key(client: TestClient) -> None:
    assert client.post("/ingest", json={}).status_code == 401


@pytest.mark.parametrize(
    "message",
    [
        {"sender": "her", "text": "hi", "source": "notification"},  # bad sender
        {"sender": "contact", "text": "", "source": "notification"},  # empty text
        {"sender": "contact", "text": "hi", "source": "carrier pigeon"},
        {
            "sender": "contact",
            "text": "hi",
            "source": "notification",
            "sent_at": "2026-10-09T21:54:01",
        },
    ],
)
def test_ingest_validation(
    client: TestClient, auth_headers: dict[str, str], contact: Contact, message: dict[str, str]
) -> None:
    body = {"contact_hint": "Sam", "messages": [message]}
    assert client.post("/ingest", json=body, headers=auth_headers).status_code == 422


# --- /import/whatsapp-export ------------------------------------------------------------


def test_import_txt_twice(
    client: TestClient, auth_headers: dict[str, str], contact: Contact, session: Session
) -> None:
    first = post_import(client, auth_headers, contact.id)
    assert first.status_code == 200, first.text  # type: ignore[attr-defined]
    assert first.json() == {  # type: ignore[attr-defined]
        "contact_id": contact.id,
        "inserted": 9,
        "skipped": 0,
        "updated": 0,
        "system_lines": 2,
        "date_order": "dmy",
    }
    second = post_import(client, auth_headers, contact.id)
    assert second.json()["inserted"] == 0  # type: ignore[attr-defined]
    assert second.json()["skipped"] == 9  # type: ignore[attr-defined]
    assert count_messages(session) == 9


def test_import_zip(client: TestClient, auth_headers: dict[str, str], contact: Contact) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("WhatsApp Chat with Sam.txt", DMY)
        archive.writestr("IMG-20260313-WA0001.jpg", b"\xff\xd8not really a jpeg")
    response = post_import(client, auth_headers, contact.id, "export.zip", buffer.getvalue())
    assert response.status_code == 200, response.text  # type: ignore[attr-defined]
    assert response.json()["inserted"] == 9  # type: ignore[attr-defined]


def test_import_then_notification_does_not_duplicate(
    client: TestClient, auth_headers: dict[str, str], contact: Contact, session: Session
) -> None:
    post_import(client, auth_headers, contact.id)
    # "Good morning!" was at 13/03/2026 09:16 IST; the notification has seconds.
    body = {
        "contact_hint": "Sam",
        "messages": [
            {
                "sender": "contact",
                "text": "Good morning!",
                "sent_at": "2026-03-13T09:16:42+05:30",
                "source": "notification",
            }
        ],
    }
    assert client.post("/ingest", json=body, headers=auth_headers).json()["inserted"] == 0
    assert count_messages(session) == 9


def test_import_unknown_author_lists_authors(
    client: TestClient, auth_headers: dict[str, str], contact: Contact
) -> None:
    response = post_import(client, auth_headers, contact.id, my_author_name="Me")
    assert response.status_code == 422  # type: ignore[attr-defined]
    assert "['Alex', 'Sam']" in response.json()["detail"]  # type: ignore[attr-defined]


def test_import_ambiguous_dates(
    client: TestClient, auth_headers: dict[str, str], contact: Contact
) -> None:
    data = (FIXTURES / "ambiguous_dates.txt").read_bytes()
    response = post_import(client, auth_headers, contact.id, data=data)
    assert response.status_code == 422  # type: ignore[attr-defined]
    fixed = post_import(client, auth_headers, contact.id, data=data, date_order="dmy")
    assert fixed.json()["inserted"] == 2  # type: ignore[attr-defined]


def test_import_rejects_other_files(
    client: TestClient, auth_headers: dict[str, str], contact: Contact
) -> None:
    response = post_import(client, auth_headers, contact.id, "photo.jpg", b"\xff\xd8")
    assert response.status_code == 422  # type: ignore[attr-defined]


def test_import_unknown_contact(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert post_import(client, auth_headers, 999).status_code == 404  # type: ignore[attr-defined]


# --- privacy ----------------------------------------------------------------------------


def test_no_message_text_in_logs(
    client: TestClient,
    auth_headers: dict[str, str],
    contact: Contact,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        post_import(client, auth_headers, contact.id)
        client.post(
            "/ingest",
            headers=auth_headers,
            json={
                "contact_hint": "Sam",
                "messages": [{"sender": "contact", "text": "secret plans", "source": "screen"}],
            },
        )
    assert caplog.records, "expected some log output"
    logged = caplog.text
    for snippet in [
        "Good morning",
        "Meeting ran late",
        "Call you at 6",
        "secret plans",
        "sleep well",
    ]:
        assert snippet not in logged
