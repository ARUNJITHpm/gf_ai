from pydantic import AwareDatetime, BaseModel, Field

from app.models.enums import MessageSource, Sender


class IngestMessageIn(BaseModel):
    sender: Sender
    text: str = Field(min_length=1, max_length=20_000)
    # Must include a UTC offset, e.g. "2026-10-09T21:54:01+05:30". Omit if unknown.
    sent_at: AwareDatetime | None = None
    source: MessageSource


class IngestRequest(BaseModel):
    contact_hint: str = Field(min_length=1, max_length=200, description="Chat title")
    messages: list[IngestMessageIn] = Field(max_length=500)


class IngestResponse(BaseModel):
    contact_id: int
    inserted: int
    skipped: int
    updated: int


class ImportResponse(IngestResponse):
    system_lines: int
    date_order: str
