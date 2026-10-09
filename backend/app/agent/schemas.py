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
