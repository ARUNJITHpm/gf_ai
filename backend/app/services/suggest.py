"""Produce reply suggestions for a contact: pause check, cache, agent run, logging."""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.graph import run_agent
from app.agent.llm import LLMClient
from app.agent.prompts import PROMPT_VERSION
from app.agent.state import AgentDeps
from app.config import Settings
from app.logging_utils import log_event
from app.models import Contact, MemoryNote, Message, PinnedFact, StyleProfile, SuggestionLog
from app.models.base import utcnow
from app.models.enums import NoteStatus, StyleStatus
from app.services.settings_store import get_setting

PAUSED_WARNING = "paused"


@dataclass
class SuggestResult:
    suggestion_id: int | None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cached: bool = False


def suggest_replies(
    session: Session,
    contact: Contact,
    *,
    llm: LLMClient,
    settings: Settings,
    tone: str | None = None,
    draft_text: str | None = None,
    now: datetime | None = None,
) -> SuggestResult:
    """Raises `DraftFailed` or `LLMError` when no suggestions could be produced."""
    now = now or utcnow()
    if get_setting(session, "paused"):
        return SuggestResult(suggestion_id=None, warnings=[PAUSED_WARNING])

    tone = (tone or get_setting(session, "default_tone")).strip()
    context_hash = _context_hash(session, contact, tone, draft_text, settings)

    cache_seconds = get_setting(session, "suggest_cache_seconds")
    if cache_seconds > 0:
        cached = session.scalar(
            select(SuggestionLog)
            .where(
                SuggestionLog.contact_id == contact.id,
                SuggestionLog.context_hash == context_hash,
                SuggestionLog.created_at >= now - timedelta(seconds=cache_seconds),
            )
            .order_by(SuggestionLog.id.desc())
        )
        if cached is not None:
            log_event("suggest.cache_hit", contact_id=contact.id, suggestion_id=cached.id)
            return SuggestResult(cached.id, cached.suggestions, cached.warnings, cached=True)

    deps = AgentDeps(
        session=session,
        llm=llm,
        settings=settings,
        now=now,
        recent_messages_n=get_setting(session, "recent_messages_n"),
        notes_top_k=get_setting(session, "notes_top_k"),
    )
    started = time.perf_counter()
    state = run_agent(deps, {"contact": contact, "tone": tone, "draft_text": draft_text})
    latency_ms = int((time.perf_counter() - started) * 1000)

    suggestions = [reply.model_dump() for reply in state.get("drafts", [])]
    warnings = state.get("warnings", [])
    usage = state["usage"]
    log = SuggestionLog(
        contact_id=contact.id,
        context_hash=context_hash,
        request={
            "message_ids": [m.id for m in state["messages"]],
            "fact_ids": [f.id for f in state["facts"]],
            "note_ids": [n.id for n in state["notes"]],
            "style_profile_id": state["style"].id if state["style"] else None,
            "draft_given": bool(draft_text),
        },
        suggestions=suggestions,
        warnings=warnings,
        tone=tone,
        prompt_version=PROMPT_VERSION,
        model=state.get("model", settings.llm_model),
        input_tokens=usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        latency_ms=latency_ms,
    )
    session.add(log)
    session.commit()
    log_event(
        "suggest.done",
        contact_id=contact.id,
        suggestion_id=log.id,
        count=len(suggestions),
        warnings=len(warnings),
        latency_ms=latency_ms,
        input_tokens=usage.input_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        output_tokens=usage.output_tokens,
    )
    return SuggestResult(log.id, suggestions, warnings)


def _context_hash(
    session: Session, contact: Contact, tone: str, draft_text: str | None, settings: Settings
) -> str:
    """Changes whenever anything the model would see changes. Cheap: aggregates only."""

    def stamp(model: Any, *conditions: Any) -> list[Any]:
        row = session.execute(
            select(func.count(), func.max(model.id), func.max(model.updated_at)).where(
                model.contact_id == contact.id, *conditions
            )
        ).one()
        return [row[0], row[1], row[2].isoformat() if row[2] else None]

    parts = {
        "contact": [contact.id, contact.updated_at.isoformat()],
        "messages": stamp(Message),
        "facts": stamp(PinnedFact, PinnedFact.active),
        "notes": stamp(MemoryNote, MemoryNote.status == NoteStatus.APPROVED),
        "style": stamp(StyleProfile, StyleProfile.status == StyleStatus.ACTIVE),
        "tone": tone,
        "draft": draft_text or "",
        "prompt": PROMPT_VERSION,
        "models": [settings.llm_model, settings.llm_fast_model],
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
