"""Shared plumbing for background jobs (spec §9).

Each job is a function `run(ctx) -> str` that returns a short status ("inserted 3 notes").
Jobs are idempotent: they read a watermark (the highest message id already processed)
from `job_state`, and write the new watermark in the same transaction as their output.
`run_job` records when each job ran and how it ended; failures never crash the app.
"""

import sys
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.llm import LLMClient
from app.config import Settings
from app.logging_utils import log_event
from app.memory.embeddings import Embedder
from app.models import JobState, Message


@dataclass
class JobContext:
    session_factory: sessionmaker[Session]
    llm: LLMClient
    embedder: Embedder
    settings: Settings
    now: datetime


JobFn = Callable[[JobContext], str]


def get_state(session: Session, job_name: str) -> JobState:
    state = session.scalar(select(JobState).where(JobState.job_name == job_name))
    if state is None:
        state = JobState(job_name=job_name)
        session.add(state)
        session.flush()
    return state


def run_job(name: str, fn: JobFn, ctx: JobContext) -> str:
    """Run one job and record the outcome in job_state. Returns the status text."""
    try:
        status = fn(ctx)
    except Exception as exc:  # noqa: BLE001 - a job failure must not take down the app
        status = f"error: {type(exc).__name__}"
        log_event("job.failed", job=name, error=type(exc).__name__)
    else:
        log_event("job.done", job=name)
    with ctx.session_factory() as session:
        state = get_state(session, name)
        state.last_run_at = ctx.now
        state.last_status = status[:200]
        session.commit()
    return status


class ProcessLock:
    """Exclusive OS-level lock on a file, held for the life of the process.

    With several server processes (e.g. uvicorn --workers 2) only the one holding the lock
    runs jobs, so nothing runs twice. The OS releases the lock if the process dies.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+b")  # noqa: SIM115 - kept open while the lock is held
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


def chunked[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def by_contact(messages: Sequence[Message]) -> dict[int, list[Message]]:
    groups: dict[int, list[Message]] = defaultdict(list)
    for message in messages:
        groups[message.contact_id].append(message)
    return dict(groups)
