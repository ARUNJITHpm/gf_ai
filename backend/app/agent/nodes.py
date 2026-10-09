"""Graph nodes. Each takes the state (plus deps) and returns the keys it changes."""

from typing import Any

from sqlalchemy import func, select

from app.agent import prompts
from app.agent.llm import Effort, LLMError, Usage
from app.agent.schemas import DraftReply, DraftSet, RiskReport
from app.agent.state import AgentDeps, AgentState
from app.logging_utils import log_event
from app.memory.retrieval import retrieve_notes
from app.models import Message, PinnedFact, StyleProfile
from app.models.enums import MessageKind, StyleStatus

N_REPLIES = 3
DRAFT_EFFORT: Effort = "low"  # chat replies don't gain much from deep thinking; keeps latency down
RISK_EFFORT: Effort = "low"
DRAFT_MAX_TOKENS = 16000
RISK_MAX_TOKENS = 8000

DECLINED_WARNING = "The model declined to suggest replies for this conversation."
RISK_UNAVAILABLE_WARNING = "The safety check couldn't run; read these replies carefully."


class DraftFailed(Exception):
    """The model returned unusable drafts twice in a row."""


def load_context(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    contact = state["contact"]
    session = deps.session
    facts = session.scalars(
        select(PinnedFact).where(PinnedFact.contact_id == contact.id, PinnedFact.active)
    ).all()
    order = func.coalesce(Message.sent_at, Message.observed_at)
    recent = session.scalars(
        select(Message)
        .where(Message.contact_id == contact.id, Message.kind != MessageKind.SYSTEM)
        .order_by(order.desc(), Message.id.desc())
        .limit(deps.recent_messages_n)
    ).all()
    style = session.scalar(
        select(StyleProfile).where(
            StyleProfile.contact_id == contact.id, StyleProfile.status == StyleStatus.ACTIVE
        )
    )
    return {"facts": list(facts), "messages": list(reversed(recent)), "style": style}


def retrieve(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    notes = retrieve_notes(
        deps.session, state["contact"].id, state["messages"], top_k=deps.notes_top_k
    )
    return {"notes": notes}


def validate_drafts(parsed: DraftSet | None, fact_ids: set[int], note_ids: set[int]) -> str | None:
    """Return what is wrong with the drafts, or None if they are usable."""
    if parsed is None:
        return "the output did not match the required JSON format"
    if len(parsed.replies) != N_REPLIES:
        return f"expected exactly {N_REPLIES} replies, got {len(parsed.replies)}"
    for i, reply in enumerate(parsed.replies):
        if not reply.text.strip():
            return f"reply {i} is empty"
        unknown_facts = set(reply.used_fact_ids) - fact_ids
        if unknown_facts:
            return f"reply {i} cites fact ids that were not provided: {sorted(unknown_facts)}"
        unknown_notes = set(reply.used_note_ids) - note_ids
        if unknown_notes:
            return f"reply {i} cites note ids that were not provided: {sorted(unknown_notes)}"
    return None


def draft(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    contact = state["contact"]
    tz = deps.settings.tz
    system = [
        prompts.DRAFT_SYSTEM,
        prompts.render_context(contact, state["facts"], state["style"]),
    ]
    fact_ids = {f.id for f in state["facts"]}
    note_ids = {n.id for n in state["notes"]}
    usage = Usage()
    problem: str | None = None

    for attempt in (1, 2):
        prompt = prompts.render_draft_prompt(
            contact=contact,
            messages=state["messages"],
            notes=state["notes"],
            tone=state["tone"],
            draft_text=state.get("draft_text"),
            now=deps.now,
            tz=tz,
            retry_problem=problem,
        )
        result = deps.llm.complete_json(
            model=deps.settings.llm_model,
            system=system,
            prompt=prompt,
            schema=DraftSet,
            effort=DRAFT_EFFORT,
            max_tokens=DRAFT_MAX_TOKENS,
        )
        usage = usage + result.usage
        if result.refused:
            log_event("agent.draft_refused", contact_id=contact.id)
            return {
                "drafts": [],
                "warnings": [DECLINED_WARNING],
                "usage": usage,
                "model": result.model,
            }
        problem = validate_drafts(result.parsed, fact_ids, note_ids)
        if problem is None:
            assert result.parsed is not None
            return {
                "drafts": result.parsed.replies,
                "warnings": [],
                "usage": usage,
                "model": result.model,
            }
        log_event("agent.draft_invalid", contact_id=contact.id, attempt=attempt)

    raise DraftFailed(problem)


def risk_check(state: AgentState, deps: AgentDeps) -> dict[str, Any]:
    drafts = state["drafts"]
    warnings = list(state["warnings"])
    prompt = prompts.render_risk_prompt(
        contact=state["contact"],
        rules=state["facts"],
        messages=state["messages"][-deps.risk_context_messages :],
        replies=[d.text for d in drafts],
        tz=deps.settings.tz,
    )
    try:
        result = deps.llm.complete_json(
            model=deps.settings.llm_fast_model,
            system=[prompts.RISK_SYSTEM],
            prompt=prompt,
            schema=RiskReport,
            effort=RISK_EFFORT,
            max_tokens=RISK_MAX_TOKENS,
        )
    except LLMError:
        log_event("agent.risk_unavailable", reason="api_error")
        return {"warnings": [*warnings, RISK_UNAVAILABLE_WARNING]}

    usage = state["usage"] + result.usage
    if result.parsed is None:
        log_event("agent.risk_unavailable", reason=result.stop_reason or "invalid")
        return {"warnings": [*warnings, RISK_UNAVAILABLE_WARNING], "usage": usage}

    kept: list[DraftReply] = []
    verdicts = {v.index: v for v in result.parsed.verdicts}
    for i, reply in enumerate(drafts):
        verdict = verdicts.get(i)
        if verdict is None or verdict.ok:
            kept.append(reply)
            continue
        problem = verdict.problem.strip() or "flagged by the safety check"
        if verdict.rewrite.strip():
            kept.append(
                reply.model_copy(
                    update={
                        "text": verdict.rewrite.strip(),
                        "reason": f"{reply.reason} (adjusted: {problem})",
                    }
                )
            )
            warnings.append(f"Suggestion {i + 1} was adjusted: {problem}")
        else:
            warnings.append(f"Suggestion {i + 1} was removed: {problem}")
    return {"drafts": kept, "warnings": warnings, "usage": usage}
