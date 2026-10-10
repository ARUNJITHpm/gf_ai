from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.llm import FakeLLM, LLMError
from app.agent.schemas import (
    ChatSummary,
    ExtractedNote,
    ExtractedNotes,
    PromiseItem,
    Promises,
    StyleDraft,
)
from app.config import Settings
from app.jobs.base import JobContext, ProcessLock, run_job
from app.jobs.scheduler import JOBS, start_scheduler
from app.memory import date_reminders, extractor, linker, promises, style, summarizer
from app.memory.embeddings import FakeEmbedder
from app.memory.retrieval import retrieve_notes
from app.models import (
    Contact,
    JobState,
    MemoryNote,
    Message,
    PinnedFact,
    Reminder,
    StyleProfile,
    SuggestionLog,
)
from app.models.enums import (
    FactCategory,
    MessageSource,
    NoteOrigin,
    NoteStatus,
    Recurrence,
    ReminderOrigin,
    Sender,
    StyleStatus,
)
from app.services.ingest import IncomingMessage, ingest_messages

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)  # 17:30 in India
ME, THEM = Sender.ME, Sender.CONTACT


@pytest.fixture
def contact(session: Session) -> Contact:
    contact = Contact(name="Nithara", wa_chat_titles=["Nithara"])
    session.add(contact)
    session.commit()
    return contact


@pytest.fixture
def fake() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def ctx(app: FastAPI, settings: Settings, fake: FakeLLM) -> JobContext:
    return JobContext(
        session_factory=app.state.session_factory,
        llm=fake,
        embedder=FakeEmbedder(),
        settings=settings,
        now=NOW,
    )


def add_messages(
    session: Session,
    contact: Contact,
    items: list[tuple[Sender, str]],
    start: datetime,
    *,
    observed: datetime | None = None,
) -> list[Message]:
    ingest_messages(
        session,
        contact,
        [
            IncomingMessage(s, t, MessageSource.MANUAL, start + timedelta(minutes=i))
            for i, (s, t) in enumerate(items)
        ],
        now=observed or start,
    )
    return list(session.scalars(select(Message).order_by(Message.id.desc()).limit(len(items))))[
        ::-1
    ]


def count(session: Session, model: type) -> int:
    session.expire_all()
    return session.scalar(select(func.count()).select_from(model)) or 0


# --- retrieval --------------------------------------------------------------------------


def test_retrieval_ranks_by_similarity_and_only_approved(
    session: Session, contact: Contact
) -> None:
    def note(text: str, status: NoteStatus = NoteStatus.APPROVED) -> MemoryNote:
        return MemoryNote(
            contact_id=contact.id, content=text, status=status, origin=NoteOrigin.MANUAL
        )

    session.add_all(
        [
            note("she loves biryani from paragon"),
            note("her exam results come out friday"),
            note("exam stress pending note", NoteStatus.PENDING),
            note("exam rejected note", NoteStatus.REJECTED),
        ]
    )
    session.commit()
    messages = add_messages(session, contact, [(THEM, "so nervous about the exam results")], NOW)
    notes = retrieve_notes(
        session, contact.id, messages, top_k=5, embedder=FakeEmbedder(), min_score=0.3
    )
    assert [n.content for n in notes] == ["her exam results come out friday"]


def test_retrieval_embeds_lazily_and_after_model_change(session: Session, contact: Contact) -> None:
    n = MemoryNote(
        contact_id=contact.id,
        content="exam friday",
        status=NoteStatus.APPROVED,
        origin=NoteOrigin.MANUAL,
    )
    session.add(n)
    session.commit()
    messages = add_messages(session, contact, [(THEM, "exam on friday")], NOW)
    retrieve_notes(session, contact.id, messages, top_k=5, embedder=FakeEmbedder())
    assert n.embedding is not None and n.embedding_model == "fake-bow-256"

    n.embedding_model = "old-model"
    retrieve_notes(session, contact.id, messages, top_k=5, embedder=FakeEmbedder())
    assert n.embedding_model == "fake-bow-256"


def test_retrieval_without_query_falls_back_to_recency(session: Session, contact: Contact) -> None:
    session.add(
        MemoryNote(
            contact_id=contact.id, content="x", status=NoteStatus.APPROVED, origin=NoteOrigin.MANUAL
        )
    )
    session.commit()
    assert len(retrieve_notes(session, contact.id, [], top_k=5, embedder=FakeEmbedder())) == 1


# --- extractor --------------------------------------------------------------------------


def test_extractor_waits_for_enough_messages(
    ctx: JobContext, session: Session, contact: Contact
) -> None:
    add_messages(session, contact, [(THEM, "hi")] * 3, NOW, observed=NOW - timedelta(hours=1))
    assert extractor.run(ctx).startswith("waiting")
    assert ctx.llm.calls == []  # type: ignore[attr-defined]


def test_extractor_inserts_pending_and_is_idempotent(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    msgs = add_messages(
        session, contact, [(THEM, f"message {i}") for i in range(55)], NOW - timedelta(hours=2)
    )
    session.add(
        MemoryNote(
            contact_id=contact.id, content="She has a cat named Mittens", origin=NoteOrigin.MANUAL
        )
    )
    session.commit()
    fake.queue(
        ExtractedNotes,
        ExtractedNotes(
            notes=[
                ExtractedNote(
                    content="She is moving to Bangalore in March",
                    source_message_ids=[msgs[0].id, 999999],
                ),
                ExtractedNote(
                    content="she has a cat named mittens", source_message_ids=[]
                ),  # exact dup
                ExtractedNote(
                    content="She has a cat named Mittens!", source_message_ids=[]
                ),  # near dup
            ]
        ),
    )
    assert extractor.run(ctx) == "inserted 1 pending notes from 55 messages"
    new = session.scalars(select(MemoryNote).where(MemoryNote.origin == NoteOrigin.EXTRACTOR)).one()
    assert new.status == NoteStatus.PENDING
    assert new.source_message_ids == [msgs[0].id]  # unknown id dropped
    assert new.embedding is not None

    assert extractor.run(ctx) == "no new messages"
    assert len(fake.calls) == 1
    assert "She has a cat named Mittens" in fake.calls[0].prompt  # known notes shown to the model


def test_extractor_api_error_keeps_watermark(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    add_messages(session, contact, [(THEM, f"m{i}") for i in range(50)], NOW)
    fake.queue(ExtractedNotes, LLMError("down"), ExtractedNotes(notes=[]))
    assert run_job("extractor", extractor.run, ctx) == "error: LLMError"
    assert extractor.run(ctx).startswith("inserted 0")  # retried, not skipped
    state = session.scalar(select(JobState).where(JobState.job_name == "extractor"))
    assert state is not None and state.last_message_id is not None


# --- promises ---------------------------------------------------------------------------


def test_promises_create_reminders_once(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    msgs = add_messages(
        session,
        contact,
        [(THEM, "call me later?"), (ME, "yes I'll call you tonight"), (ME, "and send the photos")],
        NOW - timedelta(minutes=10),
    )
    fake.queue(
        Promises,
        Promises(
            items=[
                PromiseItem(message_id=msgs[1].id, title="Call her", due_local="2026-10-09 21:00"),
                PromiseItem(message_id=msgs[2].id, title="Send photos", due_local=""),
                PromiseItem(message_id=msgs[0].id, title="Not mine", due_local=""),  # her message
            ]
        ),
    )
    assert promises.run(ctx) == "created 2 reminders from 3 messages"
    rows = session.scalars(select(Reminder).order_by(Reminder.due_at)).all()
    assert [r.title for r in rows] == ["Send photos", "Call her"]
    assert rows[1].due_at == datetime(2026, 10, 9, 15, 30, tzinfo=UTC)  # 21:00 IST
    assert rows[0].due_at == msgs[2].sent_at + timedelta(hours=3)  # type: ignore[operator]
    assert all(r.origin == ReminderOrigin.PROMISE for r in rows)

    assert promises.run(ctx) == "created 0 reminders from 0 messages"
    assert count(session, Reminder) == 2


def test_promises_skip_contacts_without_my_messages(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    add_messages(session, contact, [(THEM, "I'll call you")], NOW)
    promises.run(ctx)
    assert fake.calls == []


# --- summarizer -------------------------------------------------------------------------


def test_summarizer_old_weeks_only_and_late_imports(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    old_start = NOW - timedelta(days=60)
    add_messages(session, contact, [(THEM, f"old {i}") for i in range(12)], old_start)
    add_messages(
        session, contact, [(THEM, f"recent {i}") for i in range(12)], NOW - timedelta(days=2)
    )
    add_messages(
        session, contact, [(THEM, f"quiet {i}") for i in range(3)], NOW - timedelta(days=45)
    )
    fake.queue(ChatSummary, ChatSummary(worth_keeping=True, summary="They planned a trip to Goa."))
    assert summarizer.run(ctx).startswith("created 1 summary notes")
    note = session.scalars(select(MemoryNote)).one()
    assert note.status == NoteStatus.PENDING and note.origin == NoteOrigin.SUMMARIZER
    assert note.content.endswith("They planned a trip to Goa.")
    assert "recent" not in fake.calls[0].prompt

    # Running again finds nothing new; an old export imported later is picked up.
    assert summarizer.run(ctx).startswith("created 0 summary notes from 0")
    add_messages(
        session, contact, [(ME, f"imported {i}") for i in range(10)], NOW - timedelta(days=90)
    )
    fake.queue(ChatSummary, ChatSummary(worth_keeping=False, summary=""))
    assert summarizer.run(ctx) == "created 0 summary notes from 10 old messages"


# --- date reminders ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,recurrence,today,expected",
    [
        (date(2000, 10, 17), Recurrence.YEARLY, date(2026, 10, 9), date(2026, 10, 17)),
        (date(2000, 3, 1), Recurrence.YEARLY, date(2026, 10, 9), date(2027, 3, 1)),
        (date(2000, 2, 29), Recurrence.YEARLY, date(2026, 10, 9), date(2027, 2, 28)),
        (date(2026, 12, 25), Recurrence.NONE, date(2026, 10, 9), date(2026, 12, 25)),
        (date(2026, 1, 1), Recurrence.NONE, date(2026, 10, 9), None),
    ],
)
def test_next_occurrence(
    event: date, recurrence: Recurrence, today: date, expected: date | None
) -> None:
    assert date_reminders.next_occurrence(event, recurrence, today) == expected


def test_date_reminders_created_once(ctx: JobContext, session: Session, contact: Contact) -> None:
    session.add(
        PinnedFact(
            contact_id=contact.id,
            category=FactCategory.DATE,
            content="Her birthday",
            event_date=date(2000, 10, 17),
            recurrence=Recurrence.YEARLY,
        )
    )
    session.commit()
    assert date_reminders.run(ctx) == "created 2 date reminders"
    assert date_reminders.run(ctx) == "created 0 date reminders"
    rows = session.scalars(select(Reminder).order_by(Reminder.due_at)).all()
    assert [r.title for r in rows] == [
        "Her birthday in 7 days (17 Oct)",
        "Her birthday tomorrow (17 Oct)",
    ]
    assert rows[0].due_at == datetime(2026, 10, 10, 3, 30, tzinfo=UTC)  # 09:00 IST on 10 Oct


# --- linker and feedback ----------------------------------------------------------------


def make_log(
    session: Session, contact: Contact, texts: list[str], created: datetime
) -> SuggestionLog:
    log = SuggestionLog(
        contact_id=contact.id,
        context_hash="x",
        suggestions=[{"text": t} for t in texts],
        prompt_version="v",
        model="m",
        created_at=created,
    )
    session.add(log)
    session.commit()
    return log


def test_linker_matches_sent_message(ctx: JobContext, session: Session, contact: Contact) -> None:
    log = make_log(
        session,
        contact,
        ["see you at 8 then!", "can't wait 😊", "sounds good"],
        NOW - timedelta(minutes=5),
    )
    unrelated = make_log(session, contact, ["totally different"], NOW - timedelta(minutes=4))
    old = make_log(session, contact, ["see you at 8 then!"], NOW - timedelta(hours=2))
    msgs = add_messages(session, contact, [(ME, "see u at 8 then!!")], NOW)
    assert linker.run(ctx) == "linked 1 of 1 sent messages"
    session.expire_all()
    assert (log.final_text, log.chosen_index) == ("see u at 8 then!!", 0)
    assert unrelated.final_text is None and old.final_text is None
    assert msgs[0].linked_suggestion_id == log.id
    assert linker.run(ctx) == "linked 0 of 0 sent messages"


def test_feedback_endpoint(
    client: TestClient, auth_headers: dict[str, str], session: Session, contact: Contact
) -> None:
    log = make_log(session, contact, ["a", "b", "c"], NOW)
    url = f"/suggestions/{log.id}/feedback"
    response = client.post(url, json={"chosen_index": 1}, headers=auth_headers)
    assert response.json() == {
        "suggestion_id": log.id,
        "chosen_index": 1,
        "final_text_recorded": False,
    }
    assert client.post(url, json={"chosen_index": 3}, headers=auth_headers).status_code == 422
    assert (
        client.post(
            "/suggestions/999/feedback", json={"chosen_index": 0}, headers=auth_headers
        ).status_code
        == 404
    )
    done = client.post(url, json={"chosen_index": 1, "final_text": "b!"}, headers=auth_headers)
    assert done.json()["final_text_recorded"] is True
    assert client.post(url, json={"chosen_index": 0}).status_code == 401


# --- reminders API ----------------------------------------------------------------------


def test_reminders_due_and_done(
    client: TestClient, auth_headers: dict[str, str], session: Session, contact: Contact
) -> None:
    now = datetime.now(UTC)
    for key, due in [
        ("soon", now + timedelta(hours=2)),
        ("later", now + timedelta(days=3)),
        ("late", now - timedelta(hours=1)),
    ]:
        session.add(
            Reminder(
                contact_id=contact.id,
                title=key,
                due_at=due,
                origin=ReminderOrigin.MANUAL,
                origin_key=key,
            )
        )
    session.commit()
    due = client.get("/reminders/due", headers=auth_headers).json()
    assert [r["title"] for r in due] == ["late", "soon"]
    assert [
        r["title"]
        for r in client.get("/reminders/due?within_hours=100", headers=auth_headers).json()
    ] == ["late", "soon", "later"]

    client.post(f"/reminders/{due[0]['id']}/done", headers=auth_headers)
    assert [r["title"] for r in client.get("/reminders/due", headers=auth_headers).json()] == [
        "soon"
    ]
    assert client.post("/reminders/999/done", headers=auth_headers).status_code == 404


# --- style refresh ----------------------------------------------------------------------


def test_style_needs_enough_messages_then_drafts_once(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    add_messages(session, contact, [(ME, f"msg {i} 😄") for i in range(10)], NOW)
    assert style.run(ctx) == "drafted 0 style profiles"
    add_messages(
        session, contact, [(ME, f"more {i} da") for i in range(15)], NOW + timedelta(hours=1)
    )
    log = make_log(session, contact, ["Sounds great!"], NOW)
    log.chosen_index, log.final_text = 0, "sounds gr8 😄"
    session.commit()
    fake.queue(
        StyleDraft,
        StyleDraft(
            description="Short, lowercase, 😄 a lot.",
            default_tone="playful",
            example_indexes=[0, 1, 99],
        ),
    )
    assert style.run(ctx) == "drafted 1 style profiles"
    profile = session.scalars(select(StyleProfile)).one()
    assert profile.status == StyleStatus.PENDING and profile.version == 1
    assert profile.tone_defaults == {"default": "playful"}
    assert profile.examples == ["msg 0 😄", "msg 1 😄"]  # verbatim, bad index dropped
    assert "I sent: sounds gr8 😄" in fake.calls[0].prompt

    assert style.run(ctx) == "drafted 0 style profiles"  # nothing new since that draft


# --- infrastructure ---------------------------------------------------------------------


def test_run_job_records_status_and_survives_errors(ctx: JobContext, session: Session) -> None:
    def boom(_: JobContext) -> str:
        raise RuntimeError("secret text must not be logged")

    assert run_job("boom", boom, ctx) == "error: RuntimeError"
    assert run_job("ok", lambda _: "all good", ctx) == "all good"
    states = {s.job_name: s.last_status for s in session.scalars(select(JobState))}
    assert states == {"boom": "error: RuntimeError", "ok": "all good"}


def test_process_lock_is_exclusive(tmp_path: Path) -> None:
    first, second = ProcessLock(tmp_path / "jobs.lock"), ProcessLock(tmp_path / "jobs.lock")
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_scheduler_disabled_in_tests(app: FastAPI) -> None:
    assert start_scheduler(app) is None
    assert set(JOBS) == {"linker", "extractor", "promises", "date_reminders", "summarizer", "style"}


def test_promises_bad_due_time_and_old_messages(
    ctx: JobContext, fake: FakeLLM, session: Session, contact: Contact
) -> None:
    old = add_messages(session, contact, [(ME, "I'll bring the book")], NOW - timedelta(days=30))
    recent = add_messages(
        session, contact, [(ME, "I'll call you tonight")], NOW - timedelta(hours=1)
    )
    fake.queue(
        Promises,
        Promises(
            items=[
                PromiseItem(
                    message_id=recent[0].id, title="Call her", due_local="2020-01-01 10:00"
                ),
                PromiseItem(message_id=old[0].id, title="Bring book", due_local=""),
            ]
        ),
    )
    assert promises.run(ctx) == "created 1 reminders from 2 messages"
    reminder = session.scalars(select(Reminder)).one()
    assert reminder.title == "Call her"  # the 30-day-old promise is ignored
    assert reminder.due_at == recent[0].sent_at + timedelta(hours=3)  # type: ignore[operator]
    assert f"[id {old[0].id}]" not in fake.calls[0].prompt
