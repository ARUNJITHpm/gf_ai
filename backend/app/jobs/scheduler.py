"""In-process APScheduler running the background jobs (spec §9).

Started from the app lifespan when ENABLE_JOBS is true. Only the process holding the jobs
lock schedules anything, so running several workers never runs a job twice.
"""

from dataclasses import dataclass
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from app.agent.llm import LLMClient, make_llm
from app.config import Settings, default_data_dir
from app.jobs.base import JobContext, JobFn, ProcessLock, run_job
from app.logging_utils import log_event
from app.memory import date_reminders, extractor, linker, promises, style, summarizer
from app.models.base import utcnow


@dataclass(frozen=True)
class JobSpec:
    fn: JobFn
    every: timedelta


JOBS: dict[str, JobSpec] = {
    linker.JOB: JobSpec(linker.run, timedelta(minutes=5)),
    extractor.JOB: JobSpec(extractor.run, timedelta(minutes=15)),
    promises.JOB: JobSpec(promises.run, timedelta(minutes=15)),
    date_reminders.JOB: JobSpec(date_reminders.run, timedelta(hours=24)),
    summarizer.JOB: JobSpec(summarizer.run, timedelta(hours=24)),
    style.JOB: JobSpec(style.run, timedelta(days=7)),
}


def make_context(app: FastAPI) -> JobContext:
    settings: Settings = app.state.settings
    llm: LLMClient | None = getattr(app.state, "job_llm", None)
    if llm is None:
        llm = app.state.job_llm = make_llm(settings)
    return JobContext(
        session_factory=app.state.session_factory,
        llm=llm,
        embedder=app.state.embedder,
        settings=settings,
        now=utcnow(),
    )


def start_scheduler(app: FastAPI) -> BackgroundScheduler | None:
    settings: Settings = app.state.settings
    if not settings.enable_jobs:
        log_event("jobs.disabled", reason="ENABLE_JOBS=false")
        return None
    lock = ProcessLock(default_data_dir() / "jobs.lock")
    if not lock.acquire():
        log_event("jobs.disabled", reason="another process runs the jobs")
        return None
    app.state.jobs_lock = lock

    scheduler = BackgroundScheduler(timezone="UTC")
    start = utcnow() + timedelta(minutes=1)  # let the app finish starting first
    for i, (name, spec) in enumerate(JOBS.items()):
        scheduler.add_job(
            lambda name=name, fn=spec.fn: run_job(name, fn, make_context(app)),
            "interval",
            seconds=int(spec.every.total_seconds()),
            next_run_time=start + timedelta(seconds=20 * i),  # staggered
            id=name,
            coalesce=True,
            max_instances=1,
        )
    scheduler.start()
    log_event("jobs.started", count=len(JOBS))
    return scheduler


def stop_scheduler(app: FastAPI) -> None:
    scheduler: BackgroundScheduler | None = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    lock: ProcessLock | None = getattr(app.state, "jobs_lock", None)
    if lock is not None:
        lock.release()
