from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import FakeLLM
from app.agent.schemas import DraftReply, DraftSet, RiskReport
from app.models import Contact, MemoryNote, PinnedFact
from app.models.enums import MessageSource, NoteOrigin, NoteStatus, Sender
from app.services.ingest import IncomingMessage, ingest_messages

HINT = {"contact_hint": "Asha 🌸"}


@pytest.fixture
def contact(session: Session) -> Contact:
    contact = Contact(name="Asha", wa_chat_titles=["Asha"])
    session.add(contact)
    session.commit()
    return contact


# --- pinned facts -----------------------------------------------------------------------


def test_fact_crud(client: TestClient, auth_headers: dict[str, str], contact: Contact) -> None:
    created = client.post(
        "/memory/pinned",
        json={**HINT, "category": "rule", "content": "  Never joke about her weight "},
        headers=auth_headers,
    )
    assert created.status_code == 201
    fact = created.json()
    assert (fact["category"], fact["content"], fact["active"]) == (
        "rule",
        "Never joke about her weight",
        True,
    )

    patched = client.patch(
        f"/memory/pinned/{fact['id']}", json={"active": False}, headers=auth_headers
    ).json()
    assert patched["active"] is False and patched["content"] == "Never joke about her weight"

    listed = client.get("/memory/pinned", params=HINT, headers=auth_headers).json()
    assert [f["id"] for f in listed] == [fact["id"]]

    assert client.delete(f"/memory/pinned/{fact['id']}", headers=auth_headers).status_code == 204
    assert client.get("/memory/pinned", params=HINT, headers=auth_headers).json() == []
    assert client.delete(f"/memory/pinned/{fact['id']}", headers=auth_headers).status_code == 404


def test_date_fact_needs_a_date(
    client: TestClient, auth_headers: dict[str, str], contact: Contact
) -> None:
    body = {**HINT, "category": "date", "content": "Her birthday"}
    assert client.post("/memory/pinned", json=body, headers=auth_headers).status_code == 422
    ok = client.post(
        "/memory/pinned",
        json={**body, "event_date": "2000-10-17", "recurrence": "yearly"},
        headers=auth_headers,
    )
    assert ok.status_code == 201
    assert (ok.json()["event_date"], ok.json()["recurrence"]) == ("2000-10-17", "yearly")
    # Clearing the date of a date fact is refused too.
    fact_id = ok.json()["id"]
    cleared = client.patch(
        f"/memory/pinned/{fact_id}", json={"event_date": None}, headers=auth_headers
    )
    assert cleared.status_code == 422


def test_unknown_contact_and_auth(client: TestClient, auth_headers: dict[str, str]) -> None:
    body = {"contact_hint": "Nobody", "category": "profile", "content": "x"}
    assert client.post("/memory/pinned", json=body, headers=auth_headers).status_code == 404
    assert (
        client.get(
            "/memory/notes", params={"contact_hint": "Nobody"}, headers=auth_headers
        ).status_code
        == 404
    )
    assert client.get("/memory/pinned", params=HINT).status_code == 401


# --- notes ------------------------------------------------------------------------------


def test_notes_written_on_phone_are_approved(
    client: TestClient, auth_headers: dict[str, str], contact: Contact, session: Session
) -> None:
    created = client.post(
        "/memory/notes",
        json={**HINT, "content": "She is stressed about moving to Bangalore"},
        headers=auth_headers,
    ).json()
    assert (created["status"], created["origin"]) == ("approved", "manual")
    listed = client.get("/memory/notes", params=HINT, headers=auth_headers).json()
    assert [n["content"] for n in listed] == ["She is stressed about moving to Bangalore"]


def test_review_queue_approve_reject_delete(
    client: TestClient, auth_headers: dict[str, str], contact: Contact, session: Session
) -> None:
    pending = [
        MemoryNote(contact_id=contact.id, content=f"proposal {i}", origin=NoteOrigin.EXTRACTOR)
        for i in range(3)
    ]
    session.add_all(pending)
    session.commit()
    queue = client.get("/memory/notes", params={**HINT, "status": "pending"}, headers=auth_headers)
    assert len(queue.json()) == 3

    a, b, c = (n.id for n in pending)
    assert (
        client.post(f"/memory/notes/{a}/approve", headers=auth_headers).json()["status"]
        == "approved"
    )
    assert (
        client.post(f"/memory/notes/{b}/reject", headers=auth_headers).json()["status"]
        == "rejected"
    )
    assert client.delete(f"/memory/notes/{c}", headers=auth_headers).status_code == 204
    assert client.post("/memory/notes/999/approve", headers=auth_headers).status_code == 404

    session.expire_all()
    statuses = dict(session.execute(select(MemoryNote.content, MemoryNote.status)).all())
    assert statuses == {"proposal 0": NoteStatus.APPROVED, "proposal 1": NoteStatus.REJECTED}


def test_note_from_phone_reaches_the_prompt(
    app: FastAPI,
    client: TestClient,
    auth_headers: dict[str, str],
    contact: Contact,
    session: Session,
) -> None:
    """End to end: a thought saved in the app is used when the chat is about it."""
    client.post(
        "/memory/notes",
        json={**HINT, "content": "She is nervous about her exam results"},
        headers=auth_headers,
    )
    client.post(
        "/memory/pinned",
        json={**HINT, "category": "rule", "content": "No sarcasm"},
        headers=auth_headers,
    )
    ingest_messages(
        session,
        contact,
        [
            IncomingMessage(
                Sender.CONTACT,
                "exam results come out tomorrow",
                MessageSource.MANUAL,
                datetime.now(UTC),
            )
        ],
    )
    fake = FakeLLM()
    reply = DraftReply(text="ok", tone="warm", reason="r", used_fact_ids=[], used_note_ids=[])
    fake.queue(DraftSet, DraftSet(replies=[reply, reply, reply]))
    fake.queue(RiskReport, RiskReport(verdicts=[]))
    app.state.llm = fake
    assert client.post("/suggest", json=HINT, headers=auth_headers).status_code == 200
    prompt = "\n".join(fake.prompts_for(DraftSet))
    assert "She is nervous about her exam results" in prompt
    assert "(rule) No sarcasm" in prompt
    assert session.scalar(select(PinnedFact.content)) == "No sarcasm"
