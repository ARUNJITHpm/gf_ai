"""Import a WhatsApp export (.txt, or the .zip WhatsApp produces) for one contact.

Shared by the HTTP endpoint and the CLI.
"""

import io
import zipfile
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.logging_utils import log_event
from app.models import Contact
from app.models.enums import MessageSource
from app.services.export_parser import (
    DateOrder,
    ExportParseError,
    map_authors,
    parse_export,
)
from app.services.ingest import IncomingMessage, IngestResult, ingest_messages

MAX_TEXT_BYTES = 200 * 1024 * 1024  # uncompressed chat text; guards against zip bombs


@dataclass
class ImportResult(IngestResult):
    system_lines: int = 0
    date_order: str = DateOrder.AUTO.value


def read_export_text(filename: str, data: bytes) -> str:
    """Return the chat text from a .txt upload or from the .txt inside a .zip."""
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            texts = [info for info in archive.infolist() if info.filename.lower().endswith(".txt")]
            if not texts:
                raise ExportParseError("the .zip contains no .txt chat file")
            # WhatsApp names it "WhatsApp Chat with <name>.txt"; otherwise take the largest.
            info = max(texts, key=lambda i: ("whatsapp chat" in i.filename.lower(), i.file_size))
            if info.file_size > MAX_TEXT_BYTES:
                raise ExportParseError("chat file inside the .zip is too large")
            data = archive.read(info)
    elif not filename.lower().endswith(".txt"):
        raise ExportParseError("upload a WhatsApp export .txt or .zip")

    if len(data) > MAX_TEXT_BYTES:
        raise ExportParseError("chat file is too large")
    return data.decode("utf-8-sig", errors="replace")


def import_export(
    session: Session,
    contact: Contact,
    content: str,
    *,
    my_author_name: str,
    contact_author_name: str,
    tz: ZoneInfo,
    date_order: DateOrder = DateOrder.AUTO,
) -> ImportResult:
    parsed = parse_export(content, tz, date_order)
    labeled = map_authors(parsed, my_author_name, contact_author_name)
    result = ingest_messages(
        session,
        contact,
        [
            IncomingMessage(
                sender=sender, text=m.text, source=MessageSource.EXPORT, sent_at=m.sent_at
            )
            for sender, m in labeled
        ],
    )
    log_event(
        "import.done",
        contact_id=contact.id,
        message_count=len(labeled),
        system_lines=parsed.system_lines,
        date_order=parsed.date_order.value,
    )
    return ImportResult(
        inserted=result.inserted,
        skipped=result.skipped,
        updated=result.updated,
        system_lines=parsed.system_lines,
        date_order=parsed.date_order.value,
    )
