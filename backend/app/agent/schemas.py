"""Shapes the model must return (structured outputs, validated with Pydantic).

Counts and id rules ("exactly 3", "ids I gave you") are checked in code, not in the JSON
schema, so the error message on retry can say exactly what was wrong.
"""

from pydantic import BaseModel, Field


class DraftReply(BaseModel):
    text: str = Field(description="The reply, exactly as I would send it.")
    tone: str = Field(description="One or two words, e.g. warm, playful, apologetic.")
    reason: str = Field(description="One short sentence: why this reply fits right now.")
    used_fact_ids: list[int] = Field(description="Ids of pinned facts this reply relies on.")
    used_note_ids: list[int] = Field(description="Ids of memory notes this reply relies on.")


class DraftSet(BaseModel):
    replies: list[DraftReply]


class RiskVerdict(BaseModel):
    index: int = Field(description="0-based index of the reply being judged.")
    ok: bool
    problem: str = Field(description="Empty if ok; otherwise what is wrong, in a few words.")
    rewrite: str = Field(description="Empty if ok; otherwise a corrected reply in the same voice.")


class RiskReport(BaseModel):
    verdicts: list[RiskVerdict]


# --- background jobs --------------------------------------------------------------------


class ExtractedNote(BaseModel):
    content: str = Field(description="One durable fact, as a short third-person sentence.")
    source_message_ids: list[int] = Field(description="Ids of the messages it comes from.")


class ExtractedNotes(BaseModel):
    notes: list[ExtractedNote]


class PromiseItem(BaseModel):
    message_id: int = Field(description="Id of my message containing the commitment.")
    title: str = Field(description="Short reminder text, e.g. 'Call her tonight'.")
    due_local: str = Field(
        description="When it's due, local time 'YYYY-MM-DD HH:MM'; empty if no time is implied."
    )


class Promises(BaseModel):
    items: list[PromiseItem]


class ChatSummary(BaseModel):
    worth_keeping: bool = Field(description="False if nothing here matters later.")
    summary: str = Field(description="2-4 sentences of what's worth remembering; may be empty.")


class StyleDraft(BaseModel):
    description: str = Field(description="How I write to this person, in 3-6 sentences.")
    default_tone: str = Field(description="My usual tone, one or two words.")
    example_indexes: list[int] = Field(description="Indexes of 3-6 typical messages of mine.")
