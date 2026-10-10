"""Run the eval cases against the real model (costs a little money). Uses a throwaway DB.

    uv run python -m evals.run              # all cases
    uv run python -m evals.run upset manglish

Needs the API key for LLM_PROVIDER in backend/.env. Read the output; automatic checks only catch the
obvious failures (forbidden words, wrong count).
"""

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from app.agent.llm import LLMClient, LLMError, make_llm
from app.agent.nodes import DraftFailed
from app.agent.prompts import PROMPT_VERSION
from app.cli import use_utf8_console
from app.config import get_settings
from app.db import create_db_engine, create_session_factory
from app.models import Base, Contact, MemoryNote, PinnedFact, StyleProfile, SuggestionLog
from app.models.base import utcnow
from app.models.enums import MessageSource, NoteOrigin, NoteStatus, StyleStatus
from app.services.ingest import IncomingMessage, ingest_messages
from app.services.settings_store import seed_defaults
from app.services.suggest import suggest_replies
from evals.cases import CASES, Case


def run_case(case: Case, llm: LLMClient, db_dir: Path) -> bool:
    settings = get_settings().model_copy(
        update={"database_url": f"sqlite:///{(db_dir / f'{case.name}.db').as_posix()}"}
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    now = utcnow()
    with create_session_factory(engine)() as session:
        seed_defaults(session)
        contact = Contact(name="Nithara", wa_chat_titles=["Nithara"])
        session.add(contact)
        session.commit()
        start = now - timedelta(minutes=len(case.messages))
        ingest_messages(
            session,
            contact,
            [
                IncomingMessage(s, t, MessageSource.MANUAL, start + timedelta(minutes=i))
                for i, (s, t) in enumerate(case.messages)
            ],
            now=now,
        )
        session.add_all(
            [PinnedFact(contact_id=contact.id, category=c, content=t) for c, t in case.facts]
            + [
                MemoryNote(
                    contact_id=contact.id,
                    content=n,
                    status=NoteStatus.APPROVED,
                    origin=NoteOrigin.MANUAL,
                )
                for n in case.notes
            ]
        )
        if case.style:
            session.add(
                StyleProfile(
                    contact_id=contact.id,
                    description=case.style,
                    examples=case.style_examples,
                    status=StyleStatus.ACTIVE,
                )
            )
        session.commit()

        print(f"\n=== {case.name}: {case.goal}")
        try:
            result = suggest_replies(
                session, contact, llm=llm, settings=settings, tone=case.tone, draft_text=case.draft
            )
        except (DraftFailed, LLMError) as exc:
            print(f"  FAILED: {exc}")
            engine.dispose()
            return False

        problems: list[str] = []
        for i, s in enumerate(result.suggestions, start=1):
            print(f"  [{i}] ({s['tone']}) {s['text']}")
            print(
                f"      why: {s['reason']}  facts={s['used_fact_ids']} notes={s['used_note_ids']}"
            )
            lowered = s["text"].lower()
            problems += [f"[{i}] contains {w!r}" for w in case.must_not_contain if w in lowered]
        for warning in result.warnings:
            print(f"  ! {warning}")
        if not result.suggestions:
            problems.append("no suggestions")

        log = session.get(SuggestionLog, result.suggestion_id) if result.suggestion_id else None
        if log:
            print(f"  {log.latency_ms} ms, {log.input_tokens} in / {log.output_tokens} out")
        for problem in problems:
            print(f"  CHECK FAILED: {problem}")
    engine.dispose()
    return not problems


def main(names: list[str]) -> int:
    use_utf8_console()
    settings = get_settings()
    llm = make_llm(settings)
    cases = [c for c in CASES if not names or c.name in names]
    print(
        f"prompt {PROMPT_VERSION}, {settings.llm_provider}: "
        f"{settings.llm_model} + {settings.llm_fast_model}"
    )
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        results = {case.name: run_case(case, llm, Path(tmp)) for case in cases}
    passed = sum(results.values())
    print(f"\nautomatic checks: {passed}/{len(results)} passed (still read the replies)")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
