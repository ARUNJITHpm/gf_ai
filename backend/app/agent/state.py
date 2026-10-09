from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from sqlalchemy.orm import Session

from app.agent.llm import LLMClient, Usage
from app.agent.schemas import DraftReply
from app.config import Settings
from app.models import Contact, MemoryNote, Message, PinnedFact, StyleProfile


class AgentState(TypedDict, total=False):
    # input
    contact: Contact
    tone: str
    draft_text: str | None
    # load_context
    facts: list[PinnedFact]
    messages: list[Message]
    style: StyleProfile | None
    # retrieve_notes
    notes: list[MemoryNote]
    # draft / risk_check
    drafts: list[DraftReply]
    warnings: list[str]
    usage: Usage
    model: str


@dataclass
class AgentDeps:
    """Everything the nodes need besides the state. Built once per request."""

    session: Session
    llm: LLMClient
    settings: Settings
    now: datetime
    recent_messages_n: int = 25
    notes_top_k: int = 5
    risk_context_messages: int = 8
