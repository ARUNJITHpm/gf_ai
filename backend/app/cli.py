"""Command-line tools. Run `uv run python -m app.cli --help`.

add-contact   --name NAME --title "Chat title" [--title ...]
list-contacts
import        --file chat.zip --contact NAME_OR_ID --me "My Name" --them "Her Name"
hash-password (prompts; prints the ADMIN_PASSWORD_HASH line for .env)
suggest       --contact NAME_OR_ID [--tone warm] [--draft "my draft"]   (calls the real model)
run-job       NAME | --all   (extractor, promises, summarizer, date_reminders, linker, style)
reembed       recompute every note embedding (after changing EMBEDDING_MODEL)
"""

import argparse
import getpass
import io
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.agent.llm import LLMClient, LLMError, make_llm
from app.agent.nodes import DraftFailed
from app.config import Settings, get_settings
from app.db import create_db_engine, create_session_factory
from app.logging_utils import configure_logging
from app.memory.embeddings import FastEmbedEmbedder
from app.memory.retrieval import ensure_embeddings
from app.models import Contact, MemoryNote, Message, SuggestionLog
from app.models.base import utcnow
from app.services.export_parser import DateOrder, ExportParseError
from app.services.importer import import_export, read_export_text
from app.services.suggest import suggest_replies


def use_utf8_console() -> None:
    """Windows consoles default to cp1252, which can't print emoji from chats."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
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

    commands.add_parser("hash-password", help="hash an admin password for .env")

    sug = commands.add_parser("suggest", help="get reply suggestions (calls the Anthropic API)")
    sug.add_argument("--contact", required=True, help="contact name or id")
    sug.add_argument("--tone", help="e.g. warm, playful, apologetic (default: setting)")
    sug.add_argument("--draft", help="improve this draft instead of writing fresh replies")

    job = commands.add_parser("run-job", help="run a background job now")
    job.add_argument("name", nargs="?", help="job name; omit with --all")
    job.add_argument("--all", action="store_true", help="run every job once")

    commands.add_parser("reembed", help="recompute all note embeddings")

    args = parser.parse_args(argv)
    if args.command == "hash-password":
        return _hash_password()  # needs no settings or database

    settings = get_settings()
    configure_logging("WARNING")
    engine = create_db_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            if args.command == "add-contact":
                return _add_contact(session, args.name, args.title)
            if args.command == "list-contacts":
                return _list_contacts(session)
            if args.command == "suggest":
                return _suggest(session, args, settings)
            if args.command == "reembed":
                return _reembed(session, settings)
            if args.command == "run-job":
                return _run_jobs(engine, args, settings)
            return _import(session, args, settings.tz)
    finally:
        engine.dispose()


def _hash_password() -> int:
    password = getpass.getpass("Admin password: ")
    if len(password) < 10:
        print("error: use at least 10 characters", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat password: ") != password:
        print("error: passwords don't match", file=sys.stderr)
        return 1
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    print("Put this line in backend/.env:")
    print(f"ADMIN_PASSWORD_HASH='{hashed}'")
    return 0


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


def _suggest(
    session: Session, args: argparse.Namespace, settings: Settings, llm: LLMClient | None = None
) -> int:
    contact = _find_contact(session, args.contact)
    if contact is None:
        print(f"error: no contact {args.contact!r}; see list-contacts", file=sys.stderr)
        return 1
    if llm is None:
        llm = make_llm(settings)
    try:
        result = suggest_replies(
            session,
            contact,
            llm=llm,
            settings=settings,
            tone=args.tone,
            draft_text=args.draft,
            embedder=FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir),
        )
    except (DraftFailed, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    log = session.get(SuggestionLog, result.suggestion_id) if result.suggestion_id else None
    for i, s in enumerate(result.suggestions, start=1):
        print(f"\n[{i}] ({s['tone']}) {s['text']}")
        print(f"    why: {s['reason']}")
        if s["used_fact_ids"] or s["used_note_ids"]:
            print(f"    uses: facts {s['used_fact_ids']} notes {s['used_note_ids']}")
    for warning in result.warnings:
        print(f"\n! {warning}")
    if log is not None:
        cached = " (cached)" if result.cached else ""
        print(
            f"\nsuggestion #{log.id}{cached}: {log.latency_ms} ms, "
            f"{log.input_tokens} in / {log.output_tokens} out tokens, model {log.model}"
        )
    return 0


def _run_jobs(
    engine: Engine, args: argparse.Namespace, settings: Settings, llm: LLMClient | None = None
) -> int:
    from app.jobs.base import JobContext, run_job
    from app.jobs.scheduler import JOBS

    names = list(JOBS) if args.all else [args.name]
    unknown = [n for n in names if n not in JOBS]
    if not names or names == [None] or unknown:
        print(f"error: choose one of {', '.join(JOBS)} or --all", file=sys.stderr)
        return 1
    ctx = JobContext(
        session_factory=create_session_factory(engine),
        llm=llm or make_llm(settings),
        embedder=FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir),
        settings=settings,
        now=utcnow(),
    )
    failed = False
    for name in names:
        status = run_job(name, JOBS[name].fn, ctx)
        failed = failed or status.startswith("error")
        print(f"{name}: {status}")
    return 1 if failed else 0


def _reembed(session: Session, settings: Settings) -> int:
    embedder = FastEmbedEmbedder(settings.embedding_model, settings.model_cache_dir)
    notes = session.scalars(select(MemoryNote)).all()
    for note in notes:
        note.embedding = None
    count = ensure_embeddings(notes, embedder)
    session.commit()
    print(f"re-embedded {count} notes with {embedder.model_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
