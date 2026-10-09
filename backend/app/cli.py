"""Command-line tools. Run `uv run python -m app.cli --help`.

add-contact   --name NAME --title "Chat title" [--title ...]
list-contacts
import        --file chat.zip --contact NAME_OR_ID --me "My Name" --them "Her Name"
"""

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import create_db_engine, create_session_factory
from app.logging_utils import configure_logging
from app.models import Contact, Message
from app.services.export_parser import DateOrder, ExportParseError
from app.services.importer import import_export, read_export_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add-contact", help="create a contact")
    add.add_argument("--name", required=True)
    add.add_argument(
        "--title",
        action="append",
        required=True,
        help="WhatsApp chat title (repeat for several)",
    )

    commands.add_parser("list-contacts", help="show contacts and message counts")

    imp = commands.add_parser("import", help="import a WhatsApp export (.txt or .zip)")
    imp.add_argument("--file", required=True, type=Path)
    imp.add_argument("--contact", required=True, help="contact name or id")
    imp.add_argument("--me", required=True, help="your name as it appears in the export")
    imp.add_argument("--them", required=True, help="their name as it appears in the export")
    imp.add_argument(
        "--date-order", choices=[o.value for o in DateOrder], default=DateOrder.AUTO.value
    )

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging("WARNING")
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            if args.command == "add-contact":
                return _add_contact(session, args.name, args.title)
            if args.command == "list-contacts":
                return _list_contacts(session)
            return _import(session, args, settings.tz)
    finally:
        engine.dispose()


def _add_contact(session: Session, name: str, titles: list[str]) -> int:
    contact = Contact(name=name, wa_chat_titles=titles)
    session.add(contact)
    session.commit()
    print(f"created contact {contact.id}: {name} (titles: {titles})")
    return 0


def _list_contacts(session: Session) -> int:
    rows = session.execute(
        select(Contact, func.count(Message.id))
        .outerjoin(Message, Message.contact_id == Contact.id)
        .group_by(Contact.id)
        .order_by(Contact.id)
    ).all()
    if not rows:
        print("no contacts yet; add one with: python -m app.cli add-contact --name ... --title ...")
    for contact, count in rows:
        state = "" if contact.active else " (inactive)"
        titles = contact.wa_chat_titles
        print(f"{contact.id}: {contact.name}{state}  titles={titles}  messages={count}")
    return 0


def _find_contact(session: Session, ref: str) -> Contact | None:
    if ref.isdigit():
        return session.get(Contact, int(ref))
    return session.scalar(select(Contact).where(Contact.name == ref))


def _import(session: Session, args: argparse.Namespace, tz: ZoneInfo) -> int:
    contact = _find_contact(session, args.contact)
    if contact is None:
        print(f"error: no contact {args.contact!r}; see list-contacts", file=sys.stderr)
        return 1
    try:
        content = read_export_text(args.file.name, args.file.read_bytes())
        result = import_export(
            session,
            contact,
            content,
            my_author_name=args.me,
            contact_author_name=args.them,
            tz=tz,
            date_order=DateOrder(args.date_order),
        )
    except (ExportParseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"imported into {contact.name}: inserted={result.inserted} skipped={result.skipped} "
        f"updated={result.updated} system_lines={result.system_lines} "
        f"date_order={result.date_order}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
