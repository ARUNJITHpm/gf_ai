"""SQLAdmin at /admin: single-user login, views for every table, review actions (spec §10)."""

import hmac
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import bcrypt
from fastapi import FastAPI
from sqladmin import Admin, ModelView, action, expose
from sqladmin.authentication import AuthenticationBackend
from sqladmin.filters import BooleanFilter, StaticValuesFilter
from sqladmin.flash import Flash
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import Settings
from app.logging_utils import log_event
from app.models import (
    Contact,
    JobState,
    MemoryNote,
    Message,
    PinnedFact,
    Reminder,
    Setting,
    StyleProfile,
    SuggestionLog,
)
from app.models.enums import NoteStatus, StyleStatus
from app.security import RateLimiter
from app.services.contact_data import count_contact_data, delete_contact_data
from app.services.settings_store import validate_setting

SESSION_MAX_AGE = timedelta(hours=12)
TEMPLATES_DIR = Path(__file__).parent / "templates"


def _short(limit: int) -> Callable[[Any, Any], str]:
    """List-column formatter that truncates long text."""

    def format_(model: Any, attribute: Any) -> str:
        value = getattr(model, attribute.key if hasattr(attribute, "key") else str(attribute))
        text = "" if value is None else str(value)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    return format_


def _selected_pks(request: Request) -> list[int]:
    return [int(pk) for pk in request.query_params.get("pks", "").split(",") if pk.isdigit()]


def _back(request: Request, view: ModelView) -> RedirectResponse:
    target = request.headers.get("referer") or str(
        request.url_for("admin:list", identity=view.identity)
    )
    return RedirectResponse(target, status_code=302)


async def _in_thread[T](view: ModelView, work: Callable[[Session], T]) -> T:
    """Run blocking DB work off the event loop (admin handlers are async, our DB is sync)."""
    factory = view.session_maker
    assert isinstance(factory, sessionmaker), "admin is mounted with a sync session factory"

    def run() -> T:
        with factory() as session:
            return work(session)

    return await anyio.to_thread.run_sync(run)


# --- authentication ---------------------------------------------------------------------


class AdminAuth(AuthenticationBackend):
    """One admin user. Password is checked against a bcrypt hash from .env."""

    def __init__(self, settings: Settings) -> None:
        assert settings.secret_key is not None and settings.admin_password_hash is not None
        super().__init__(
            secret_key=settings.secret_key.get_secret_value(),
            session_cookie="ra_admin",
            max_age=int(SESSION_MAX_AGE.total_seconds()),
            same_site="lax",
            https_only=settings.admin_https_only,
        )
        self._user = settings.admin_user
        self._hash = settings.admin_password_hash.get_secret_value().encode()
        self._limiter = RateLimiter(rate=5, per=60)  # login attempts per client

    async def login(self, request: Request) -> bool:
        client = request.client.host if request.client else "unknown"
        if not self._limiter.allow(client):
            log_event("admin.login_rate_limited")
            return False
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        # Always run bcrypt so a wrong username takes as long as a wrong password.
        password_ok = await anyio.to_thread.run_sync(
            lambda: bcrypt.checkpw(password.encode(), self._hash)
        )
        user_ok = hmac.compare_digest(username.encode(), self._user.encode())
        if not (password_ok and user_ok):
            log_event("admin.login_failed")
            return False
        request.session.clear()
        request.session.update({"user_id": self._user, "login_at": datetime.now(UTC).isoformat()})
        log_event("admin.login")
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        if request.session.get("user_id") != self._user:
            return False
        try:
            login_at = datetime.fromisoformat(request.session["login_at"])
        except (KeyError, ValueError):
            return False
        return datetime.now(UTC) - login_at < SESSION_MAX_AGE


# --- views ------------------------------------------------------------------------------


class ContactAdmin(ModelView, model=Contact):
    icon = "fa-solid fa-user"
    column_list = [Contact.id, Contact.name, Contact.wa_chat_titles, Contact.active]
    column_searchable_list = [Contact.name]
    form_columns = [
        Contact.name,
        Contact.wa_chat_titles,
        Contact.nicknames,
        Contact.language_notes,
        Contact.active,
    ]
    column_labels = {Contact.wa_chat_titles: "WhatsApp chat titles (JSON list)"}

    @action(
        name="delete_data",
        label="Delete all data for contact",
        add_in_list=False,
        add_in_detail=True,
    )
    async def delete_data(self, request: Request) -> Response:
        pks = _selected_pks(request)
        if len(pks) != 1:
            Flash.error(request, "Open one contact and use the action from its detail page.")
            return _back(request, self)
        return RedirectResponse(
            request.url_for("admin:view-contact-delete_data_page", pk=str(pks[0])), status_code=302
        )

    @expose("/delete-data/{pk}", methods=["GET", "POST"])
    async def delete_data_page(self, request: Request) -> Response:
        pk = int(request.path_params["pk"])
        contact = await _in_thread(self, lambda s: s.get(Contact, pk))
        if contact is None:
            return RedirectResponse(request.url_for("admin:list", identity=self.identity), 302)

        error = None
        if request.method == "POST":
            typed = str((await request.form()).get("confirm_name", ""))
            if typed.strip() == contact.name:
                counts = await _in_thread(self, lambda s: delete_contact_data(s, pk))
                Flash.success(
                    request, f"Deleted {sum(counts.values())} rows for {contact.name}.", "Done"
                )
                return RedirectResponse(
                    request.url_for("admin:details", identity=self.identity, pk=str(pk)), 302
                )
            error = "The name you typed doesn't match. Nothing was deleted."

        counts = await _in_thread(self, lambda s: count_contact_data(s, pk))
        return await self.templates.TemplateResponse(
            request,
            "delete_contact_data.html",
            {"contact": contact, "counts": counts, "error": error, "title": "Delete contact data"},
        )


class PinnedFactAdmin(ModelView, model=PinnedFact):
    icon = "fa-solid fa-thumbtack"
    column_list = [
        PinnedFact.id,
        PinnedFact.contact,
        PinnedFact.category,
        PinnedFact.content,
        PinnedFact.event_date,
        PinnedFact.recurrence,
        PinnedFact.active,
    ]
    column_formatters = {PinnedFact.content: _short(80)}
    column_searchable_list = [PinnedFact.content]
    column_filters = [BooleanFilter(PinnedFact.active)]
    form_columns = [
        PinnedFact.contact,
        PinnedFact.category,
        PinnedFact.content,
        PinnedFact.event_date,
        PinnedFact.recurrence,
        PinnedFact.active,
    ]


class MemoryNoteAdmin(ModelView, model=MemoryNote):
    """The review queue: pending notes are shown by default; approve or reject in bulk."""

    name_plural = "Memory notes (review queue)"
    icon = "fa-solid fa-brain"
    column_list = [
        MemoryNote.id,
        MemoryNote.contact,
        MemoryNote.status,
        MemoryNote.origin,
        MemoryNote.content,
        MemoryNote.created_at,
    ]
    column_formatters = {MemoryNote.content: _short(100)}
    column_searchable_list = [MemoryNote.content]
    column_filters = [
        StaticValuesFilter(
            MemoryNote.status,
            values=[(s.value, s.value.title()) for s in NoteStatus],
            default_value=NoteStatus.PENDING.value,
        )
    ]
    column_default_sort = [(MemoryNote.created_at, True)]
    form_columns = [MemoryNote.contact, MemoryNote.content, MemoryNote.status, MemoryNote.origin]
    column_details_exclude_list = [MemoryNote.embedding]

    async def _set_status(self, request: Request, status: NoteStatus) -> Response:
        pks = _selected_pks(request)
        if pks:
            changed = await _in_thread(self, lambda s: set_note_status(s, pks, status))
            Flash.success(request, f"{status.value.title()}: {changed} note(s).")
        return _back(request, self)

    @action(name="approve", label="Approve", confirmation_message="Approve the selected notes?")
    async def approve(self, request: Request) -> Response:
        return await self._set_status(request, NoteStatus.APPROVED)

    @action(name="reject", label="Reject", confirmation_message="Reject the selected notes?")
    async def reject(self, request: Request) -> Response:
        return await self._set_status(request, NoteStatus.REJECTED)


def set_note_status(session: Session, ids: Sequence[int], status: NoteStatus) -> int:
    notes = session.scalars(select(MemoryNote).where(MemoryNote.id.in_(ids))).all()
    for note in notes:
        note.status = status
        # Phase 5 (P5.2): compute the embedding here when a note is approved.
    session.commit()
    log_event("notes.status", status=status.value, count=len(notes))
    return len(notes)


class StyleProfileAdmin(ModelView, model=StyleProfile):
    icon = "fa-solid fa-pen-nib"
    column_list = [
        StyleProfile.id,
        StyleProfile.contact,
        StyleProfile.status,
        StyleProfile.version,
        StyleProfile.description,
    ]
    column_formatters = {StyleProfile.description: _short(100)}
    form_columns = [
        StyleProfile.contact,
        StyleProfile.description,
        StyleProfile.tone_defaults,
        StyleProfile.examples,
        StyleProfile.status,
        StyleProfile.version,
    ]

    @action(
        name="activate",
        label="Activate",
        confirmation_message="Make this the active style? The current one will be archived.",
    )
    async def activate(self, request: Request) -> Response:
        pks = _selected_pks(request)
        if len(pks) != 1:
            Flash.error(request, "Select exactly one style profile to activate.")
        else:
            await _in_thread(self, lambda s: activate_style(s, pks[0]))
            Flash.success(request, "Style profile activated.")
        return _back(request, self)


def activate_style(session: Session, profile_id: int) -> None:
    profile = session.get(StyleProfile, profile_id)
    if profile is None:
        return
    session.execute(
        update(StyleProfile)
        .where(
            StyleProfile.contact_id == profile.contact_id,
            StyleProfile.status == StyleStatus.ACTIVE,
            StyleProfile.id != profile.id,
        )
        .values(status=StyleStatus.ARCHIVED)
    )
    profile.status = StyleStatus.ACTIVE
    session.commit()


class ReminderAdmin(ModelView, model=Reminder):
    icon = "fa-solid fa-bell"
    column_list = [
        Reminder.id,
        Reminder.contact,
        Reminder.title,
        Reminder.due_at,
        Reminder.origin,
        Reminder.done,
    ]
    column_filters = [BooleanFilter(Reminder.done)]
    column_default_sort = [(Reminder.due_at, False)]
    form_columns = [
        Reminder.contact,
        Reminder.title,
        Reminder.due_at,
        Reminder.origin,
        Reminder.origin_key,
        Reminder.done,
    ]


class SettingAdmin(ModelView, model=Setting):
    icon = "fa-solid fa-sliders"
    column_list = [Setting.key, Setting.value, Setting.updated_at]
    form_columns = [Setting.key, Setting.value]
    column_labels = {Setting.value: 'Value (JSON: true/false, numbers, "text")'}
    can_delete = False

    async def on_model_change(
        self, data: dict[str, Any], model: Any, is_created: bool, request: Request
    ) -> None:
        key = data.get("key", model.key if model is not None else None)
        validate_setting(str(key), data.get("value"))  # ValueError is shown on the form


class MessageAdmin(ModelView, model=Message):
    icon = "fa-solid fa-comments"
    can_create = False
    can_edit = False
    column_list = [
        Message.id,
        Message.contact,
        Message.sender,
        Message.kind,
        Message.source,
        Message.sent_at,
        Message.text,
    ]
    column_formatters = {Message.text: _short(80)}
    column_searchable_list = [Message.text]
    column_default_sort = [(Message.id, True)]
    page_size = 50


class SuggestionLogAdmin(ModelView, model=SuggestionLog):
    icon = "fa-solid fa-lightbulb"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        SuggestionLog.id,
        SuggestionLog.contact,
        SuggestionLog.created_at,
        SuggestionLog.tone,
        SuggestionLog.chosen_index,
        SuggestionLog.model,
        SuggestionLog.input_tokens,
        SuggestionLog.output_tokens,
        SuggestionLog.latency_ms,
    ]
    column_default_sort = [(SuggestionLog.id, True)]


class JobStateAdmin(ModelView, model=JobState):
    icon = "fa-solid fa-gears"
    can_create = False
    can_edit = False
    can_delete = False
    column_list = [
        JobState.job_name,
        JobState.last_run_at,
        JobState.last_status,
        JobState.last_message_id,
    ]


VIEWS: list[type[ModelView]] = [
    ContactAdmin,
    PinnedFactAdmin,
    MemoryNoteAdmin,
    StyleProfileAdmin,
    ReminderAdmin,
    SettingAdmin,
    MessageAdmin,
    SuggestionLogAdmin,
    JobStateAdmin,
]


def mount_admin(app: FastAPI, settings: Settings) -> Admin | None:
    """Mount /admin. Skipped (with a log line) until the admin password and secret are set."""
    if settings.admin_password_hash is None or settings.secret_key is None:
        log_event("admin.disabled", reason="set ADMIN_PASSWORD_HASH and SECRET_KEY")
        return None
    admin = Admin(
        app,
        session_maker=app.state.session_factory,
        authentication_backend=AdminAuth(settings),
        title="Reply Assistant",
        base_url="/admin",
        templates_dir=str(TEMPLATES_DIR),
    )
    for view in VIEWS:
        admin.add_view(view)
    return admin
