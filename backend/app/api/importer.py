from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.models import Contact
from app.schemas.ingest import ImportResponse
from app.services.export_parser import DateOrder, ExportParseError
from app.services.importer import MAX_TEXT_BYTES, import_export, read_export_text

router = APIRouter()


@router.post("/import/whatsapp-export", response_model=ImportResponse)
def import_whatsapp_export(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, File(description="WhatsApp export .txt or .zip")],
    contact_id: Annotated[int, Form()],
    my_author_name: Annotated[str, Form(min_length=1, max_length=100)],
    contact_author_name: Annotated[str, Form(min_length=1, max_length=100)],
    date_order: Annotated[DateOrder, Form()] = DateOrder.AUTO,
) -> ImportResponse:
    """Import an exported chat. Safe to run again: already-stored messages are skipped."""
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown contact")

    data = file.file.read(MAX_TEXT_BYTES + 1)
    settings: Settings = request.app.state.settings
    try:
        content = read_export_text(file.filename or "", data)
        result = import_export(
            session,
            contact,
            content,
            my_author_name=my_author_name,
            contact_author_name=contact_author_name,
            tz=settings.tz,
            date_order=date_order,
        )
    except ExportParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    return ImportResponse(
        contact_id=contact.id,
        inserted=result.inserted,
        skipped=result.skipped,
        updated=result.updated,
        system_lines=result.system_lines,
        date_order=result.date_order,
    )
