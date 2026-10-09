from collections.abc import Iterator
from datetime import date

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Contact, MemoryNote, Message, PinnedFact, Setting, StyleProfile
from app.models.enums import (
    FactCategory,
    MessageSource,
    NoteOrigin,
    NoteStatus,
    Sender,
    StyleStatus,
)
from tests.conftest import TEST_API_KEY

PASSWORD = "correct-horse-battery"
# Low cost factor keeps the tests fast; the CLI uses bcrypt's default.
PASSWORD_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


def admin_settings(db_url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "api_key": TEST_API_KEY,
        "database_url": db_url,
        "admin_user": "admin",
        "admin_password_hash": PASSWORD_HASH,
        "secret_key": "test-secret-key-0123456789",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


@pytest.fixture
def settings(db_url: str) -> Settings:
    return admin_settings(db_url)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, follow_redirects=False) as client:
        yield client


def login(client: TestClient, password: str = PASSWORD, username: str = "admin") -> int:
    response = client.post("/admin/login", data={"username": username, "password": password})
    return response.status_code


@pytest.fixture
def logged_in(client: TestClient) -> TestClient:
    assert login(client) == 302
    return client


@pytest.fixture
def two_contacts(session: Session) -> tuple[Contact, Contact]:
    a = Contact(name="Sam", wa_chat_titles=["Sam"])
    b = Contact(name="Other", wa_chat_titles=["Other"])
    session.add_all([a, b])
    session.commit()
    return a, b


# --- login ------------------------------------------------------------------------------


def test_admin_requires_login(client: TestClient) -> None:
    for path in ["/admin/", "/admin/contact/list", "/admin/memory-note/list"]:
        response = client.get(path)
        assert response.status_code == 302, path
        assert response.headers["location"].endswith("/admin/login")


def test_login_and_logout(client: TestClient) -> None:
    assert login(client) == 302
    assert client.get("/admin/contact/list").status_code == 200
    cookie = client.cookies.get("ra_admin")
    assert cookie

    client.get("/admin/logout")
    assert client.get("/admin/contact/list").status_code == 302


@pytest.mark.parametrize(
    "username,password", [("admin", "wrong-password"), ("root", PASSWORD), ("", "")]
)
def test_wrong_credentials_rejected(client: TestClient, username: str, password: str) -> None:
    assert login(client, password=password, username=username) != 302
    assert client.get("/admin/contact/list").status_code == 302


def test_login_is_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        login(client, password="wrong-password")
    assert login(client) != 302  # even the right password, until the bucket refills


def test_session_cookie_flags(client: TestClient) -> None:
    response = client.post("/admin/login", data={"username": "admin", "password": PASSWORD})
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


def test_admin_not_mounted_without_password(db_url: str) -> None:
    other = create_app(admin_settings(db_url, admin_password_hash=None))
    with TestClient(other, follow_redirects=False) as client:
        assert client.get("/admin/").status_code == 404


def test_admin_is_not_behind_api_key(client: TestClient) -> None:
    # The admin uses its own login; the API key check must not block the login page.
    assert client.get("/admin/login").status_code == 200


# --- views and actions ------------------------------------------------------------------


def test_add_pinned_fact(
    logged_in: TestClient, two_contacts: tuple[Contact, Contact], session: Session
) -> None:
    sam, _ = two_contacts
    response = logged_in.post(
        "/admin/pinned-fact/create",
        data={
            "contact": str(sam.id),
            "category": "date",
            "content": "Birthday",
            "event_date": "2000-05-17",
            "recurrence": "yearly",
            "active": "y",
        },
    )
    assert response.status_code == 302, response.text
    fact = session.scalars(select(PinnedFact)).one()
    assert (fact.category, fact.event_date) == (FactCategory.DATE, date(2000, 5, 17))


def test_review_queue_shows_pending_by_default(
    logged_in: TestClient, two_contacts: tuple[Contact, Contact], session: Session
) -> None:
    sam, _ = two_contacts
    session.add_all(
        [
            MemoryNote(contact_id=sam.id, content="pending-note", origin=NoteOrigin.EXTRACTOR),
            MemoryNote(
                contact_id=sam.id,
                content="approved-note",
                origin=NoteOrigin.EXTRACTOR,
                status=NoteStatus.APPROVED,
            ),
        ]
    )
    session.commit()
    page = logged_in.get("/admin/memory-note/list").text
    assert "pending-note" in page
    assert "approved-note" not in page


def test_bulk_approve_and_reject(
    logged_in: TestClient, two_contacts: tuple[Contact, Contact], session: Session
) -> None:
    sam, _ = two_contacts
    notes = [
        MemoryNote(contact_id=sam.id, content=f"note {i}", origin=NoteOrigin.EXTRACTOR)
        for i in range(3)
    ]
    session.add_all(notes)
    session.commit()
    ids = [n.id for n in notes]

    response = logged_in.get(f"/admin/memory-note/action/approve?pks={ids[0]},{ids[1]}")
    assert response.status_code == 302
    logged_in.get(f"/admin/memory-note/action/reject?pks={ids[2]}")

    session.expire_all()
    statuses = session.scalars(select(MemoryNote.status).order_by(MemoryNote.id)).all()
    assert statuses == [NoteStatus.APPROVED, NoteStatus.APPROVED, NoteStatus.REJECTED]


def test_actions_require_login(
    client: TestClient, two_contacts: tuple[Contact, Contact], session: Session
) -> None:
    session.add(MemoryNote(contact_id=two_contacts[0].id, content="x", origin=NoteOrigin.MANUAL))
    session.commit()
    response = client.get("/admin/memory-note/action/approve?pks=1")
    assert response.status_code == 302
    assert response.headers["location"].endswith("/admin/login")
    session.expire_all()
    assert session.scalars(select(MemoryNote.status)).one() == NoteStatus.PENDING


def test_activate_style_archives_previous(
    logged_in: TestClient, two_contacts: tuple[Contact, Contact], session: Session
) -> None:
    sam, other = two_contacts
    old = StyleProfile(contact_id=sam.id, description="old", status=StyleStatus.ACTIVE)
    new = StyleProfile(contact_id=sam.id, description="new", status=StyleStatus.PENDING)
    others = StyleProfile(contact_id=other.id, description="other", status=StyleStatus.ACTIVE)
    session.add_all([old, new, others])
    session.commit()

    logged_in.get(f"/admin/style-profile/action/activate?pks={new.id}")
    session.expire_all()
    assert (old.status, new.status, others.status) == (
        StyleStatus.ARCHIVED,
        StyleStatus.ACTIVE,
        StyleStatus.ACTIVE,
    )


def test_settings_seeded_and_validated(logged_in: TestClient, session: Session) -> None:
    keys = set(session.scalars(select(Setting.key)))
    assert {"paused", "default_tone", "recent_messages_n"} <= keys
    paused = session.scalars(select(Setting).where(Setting.key == "paused")).one()

    bad = logged_in.post(f"/admin/setting/edit/{paused.id}", data={"key": "paused", "value": "5"})
    assert bad.status_code == 400
    assert "paused must be a bool" in bad.text

    good = logged_in.post(
        f"/admin/setting/edit/{paused.id}", data={"key": "paused", "value": "true"}
    )
    assert good.status_code == 302
    session.expire_all()
    assert paused.value is True


# --- delete all data for a contact ------------------------------------------------------


def test_delete_contact_data_needs_typed_name(
    logged_in: TestClient, two_contacts: tuple[Contact, Contact], session: Session
) -> None:
    sam, other = two_contacts
    for contact in (sam, other):
        session.add_all(
            [
                Message(
                    contact_id=contact.id,
                    sender=Sender.CONTACT,
                    text="hi",
                    source=MessageSource.MANUAL,
                    dedupe_hash="0" * 64,
                ),
                PinnedFact(contact_id=contact.id, category=FactCategory.RULE, content="be kind"),
                MemoryNote(contact_id=contact.id, content="n", origin=NoteOrigin.MANUAL),
            ]
        )
    session.commit()

    # The action on the detail page leads to the confirmation page.
    response = logged_in.get(f"/admin/contact/action/delete-data?pks={sam.id}")
    assert response.headers["location"].endswith(f"/admin/contact/delete-data/{sam.id}")

    page = logged_in.get(f"/admin/contact/delete-data/{sam.id}")
    assert page.status_code == 200
    assert "Type <strong>Sam</strong> to confirm" in page.text

    wrong = logged_in.post(f"/admin/contact/delete-data/{sam.id}", data={"confirm_name": "sam"})
    assert "Nothing was deleted" in wrong.text
    session.expire_all()
    assert len(session.scalars(select(Message)).all()) == 2

    done = logged_in.post(f"/admin/contact/delete-data/{sam.id}", data={"confirm_name": "Sam"})
    assert done.status_code == 302
    session.expire_all()
    remaining = session.scalars(select(Message.contact_id)).all()
    assert remaining == [other.id]
    assert session.scalars(select(PinnedFact.contact_id)).all() == [other.id]
    assert session.scalars(select(MemoryNote.contact_id)).all() == [other.id]
    assert session.get(Contact, sam.id) is not None  # the contact itself stays
