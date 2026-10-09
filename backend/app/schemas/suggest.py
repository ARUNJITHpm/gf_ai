from pydantic import AwareDatetime, BaseModel, Field

from app.models.enums import MessageSource, Sender


class VisibleMessage(BaseModel):
    sender: Sender
    text: str = Field(min_length=1, max_length=20_000)
    sent_at: AwareDatetime | None = None
    source: MessageSource = MessageSource.SCREEN


class SuggestRequest(BaseModel):
    contact_hint: str = Field(min_length=1, max_length=200, description="Chat title")
    visible_messages: list[VisibleMessage] = Field(default_factory=list, max_length=200)
    draft_text: str | None = Field(default=None, max_length=5_000)
    tone: str | None = Field(default=None, max_length=40)


class Suggestion(BaseModel):
    text: str
    tone: str
    reason: str
    used_fact_ids: list[int]
    used_note_ids: list[int]


class SuggestResponse(BaseModel):
    suggestion_id: int | None
    suggestions: list[Suggestion]
    warnings: list[str]
