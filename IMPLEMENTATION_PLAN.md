# Implementation Plan — WhatsApp Reply Assistant

Companion to `project.md` (the spec). This file is the **order of work**. Section references like §6 point to `project.md`.

How to use it:
- Work top to bottom. Each task is small enough for one commit (prefix the commit with the task id, e.g. `P2.3`).
- Every task lists **Files**, **Do**, and **Done when**. Do not move on until "Done when" is true.
- Each phase ends with a **Gate** — the acceptance checks from §13. Tick boxes as you go.
- Time estimates assume evenings/weekends, part-time.

```
P0 Setup + Android spike   ~1–2 days   <- de-risks the whole idea
P1 Backend skeleton        ~1–2 days
P2 Ingest + import         ~2–3 days   <- first real value: your history in a DB
P3 Admin                   ~1 day
P4 Agent + /suggest + CLI  ~3–4 days   <- usable from the terminal
P5 Memory + jobs           ~3–4 days
P6 Android client          ~5–8 days   <- usable on the phone
P7 Hardening (optional)    ongoing
```

---

## Phase 0 — Setup and Android feasibility spike

Goal: prove the risky part (reading WhatsApp's screen and inserting text) works on *your* phone before writing the backend.

- [ ] **P0.1 Toolchain**
  - Do: install Python 3.12, `uv`, Git, Android SDK (+ platform tools, JDK 17), Tailscale on PC and phone. Enable USB debugging on the phone.
  - Status: Python 3.12, uv, Git, adb, JDK 17 and Android SDK (`D:\Android\Sdk`, android-37) are installed. Still to do: Tailscale (PC + phone), connect the phone.
  - Done when: `uv --version`, `git --version`, `adb devices` (shows the phone) all work.

- [x] **P0.2 Repo**
  - Do: use the current project folder (`gf_ai`) as the repo. `git init`, add `.gitignore` (§12).
  - Done when: first commit exists; `.env` and `*.db` are ignored.

> Spike app is built (`spikes/a11y-dump`, AGP 9.3.3, Gradle 9.8.1, compileSdk 37). Run steps: `spikes/README.md`. Record results in `spikes/notes.md`.

- [x] **P0.3 Accessibility spike app** (throwaway, `spikes/a11y-dump/`)
  - Do: Android Studio "Empty Activity" project. Add an `AccessibilityService` with `packageNames="com.whatsapp"`, `canRetrieveWindowContent=true`, `flagReportViewIds`. On `TYPE_WINDOW_CONTENT_CHANGED`, walk `rootInActiveWindow` and `Log.d` each node: class, viewIdResourceName, text, contentDescription, boundsInScreen.
  - Done when: in `adb logcat`, with a chat open, you see message texts with left/right bounds, the chat title node, and the input field node id.

- [x] **P0.4 Insert-text spike**
  - Do: from the service, find the input `EditText` and call `ACTION_SET_TEXT` with "test from spike". Do **not** touch the send button.
  - Done when: the text appears in WhatsApp's box and can be edited/sent by hand.

- [x] **P0.5 Notification spike**
  - Do: add a `NotificationListenerService`, log `EXTRA_TITLE`, `EXTRA_MESSAGES` (sender, text, time) and `EXTRA_IS_GROUP_CONVERSATION` for `com.whatsapp`.
  - Done when: a message from a test contact appears in logcat with its timestamp. Save a few redacted log samples to `spikes/notes.md` — they become test fixtures in P6.

**Gate P0: ✅ passed 2026-10-09** (results: `spikes/notes.md`). P0.3–P0.5 work on your phone. If any fails, stop and adjust the design (§11) before Phase 1.

---

## Phase 1 — Backend skeleton

- [x] **P1.1 Project init**
  - Files: `backend/pyproject.toml`, `backend/.python-version`
  - Do: `uv init`, add deps from §2 (fastapi, uvicorn, pydantic-settings, sqlalchemy, alembic, httpx, pytest, ruff, mypy). Configure ruff (line length, isort) and mypy (strict on `app/`).
  - Done when: `uv run pytest` (0 tests) and `uv run ruff check .` run.

- [x] **P1.2 Config**
  - Files: `app/config.py`, `.env.example`
  - Do: `Settings` with every variable in §12, sensible defaults, `DATABASE_URL` default per §12. `get_settings()` cached.
  - Done when: test loads settings from a temp `.env`; missing `API_KEY` raises a clear error.

- [x] **P1.3 DB engine + session**
  - Files: `app/db.py`
  - Do: sync engine; on connect set `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`. `get_session()` dependency. Test fixture that creates a temp DB per test.
  - Done when: test confirms foreign keys are enforced.

- [x] **P1.4 Models**
  - Files: `app/models/*.py` (base with id/created_at/updated_at, + every table in §5)
  - Done when: `Base.metadata.create_all` works in a test; enums are string enums.

- [x] **P1.5 Alembic**
  - Files: `alembic.ini`, `alembic/env.py`, first revision
  - Do: `render_as_batch=True`; URL from settings; autogenerate initial migration and review it.
  - Done when: `alembic upgrade head` on an empty file creates all tables; `alembic downgrade base` drops them.

- [x] **P1.6 Security**
  - Files: `app/security.py`
  - Do: `require_api_key` dependency with `hmac.compare_digest`; simple in-memory token-bucket `RateLimiter(rate, per)` dependency factory.
  - Done when: unit tests for correct/incorrect/missing key, and limiter allowing N then 429.

- [x] **P1.7 App + health**
  - Files: `app/main.py`, `app/api/health.py`
  - Do: app factory with lifespan; `/health` returns `{status, db, version}` (db = `SELECT 1`). A placeholder authed router to prove 401s.
  - Done when: `uv run uvicorn app.main:app --host 127.0.0.1` starts; tests: `/health` 200 without key, other route 401 without key.

- [x] **P1.8 Logging**
  - Do: stdlib logging config, JSON or plain, `LOG_LEVEL` from settings. Helper `log_event(name, **ids_and_counts)`.
  - Done when: a test asserts the helper refuses/strips a `text=` kwarg.

- [x] **P1.9 README v1** — setup, run, test commands; "Decisions" section.

**Gate P1: ✅ passed 2026-10-09** (32 tests; ruff + mypy strict clean; server smoke-tested). §13 Phase 1 checks pass; ruff + mypy clean.

---

## Phase 2 — Ingest and import

- [x] **P2.1 Text normalization**
  - Files: `app/services/ingest.py` (`normalize_text`, `classify_kind`)
  - Do: per §6.1; detect `media`, `deleted`, `system` kinds from known phrases (keep phrases in one list).
  - Done when: table-driven tests incl. U+202F, LRM, edited suffix, emojis unchanged.

- [x] **P2.2 Contact matching**
  - Do: `match_contact(session, hint)` against `wa_chat_titles`, case-insensitive, emoji/whitespace-insensitive.
  - Done when: tests for exact, case, emoji variants, unknown → None.

- [x] **P2.3 Dedupe core**
  - Do: `ingest_messages(session, contact, messages) -> {inserted, skipped, updated}` implementing §6.2–6.4 (hash, ±2 min window, null-sent_at fill-in, sequence alignment for timestamp-less batches).
  - Done when: tests: same msg twice → 1 row; same msg 1 min apart from 2 sources → 1 row; "ok" sent twice an hour apart → 2 rows; screen batch overlapping stored tail → only the new suffix inserted; screen batch with repeated "ok" handled by alignment.

- [x] **P2.4 `/ingest` endpoint**
  - Files: `app/api/ingest.py`, `app/schemas/ingest.py`
  - Done when: API tests for 200 counts, 404 unknown contact, 401 without key, 422 bad body.

- [x] **P2.5 Export parser — line format**
  - Files: `app/services/export_parser.py` (pure, no DB)
  - Do: regexes for Android formats: `dd/mm/yy, hh:mm - Name: text`, `m/d/yy, h:mm AM - Name: text`, 2- and 4-digit years, 12h/24h, optional U+202F. Continuation lines append to the previous message. Lines without `Name:` are system lines.
  - Done when: tests with synthetic fixture files in `tests/fixtures/exports/`.

- [x] **P2.6 Export parser — date order + timezone**
  - Do: `date_order=auto` scans all dates; any first field > 12 → dmy, any second field > 12 → mdy; still ambiguous → error asking for explicit order. Convert local → UTC via `TIMEZONE`.
  - Done when: tests for each case including the ambiguous error.

- [x] **P2.7 Author mapping**
  - Do: map `my_author_name` → `me`, `contact_author_name` → `contact`; any other author → error listing the authors found (helps the user pick names).
  - Done when: tests incl. a "names found" error message.

- [x] **P2.8 `/import/whatsapp-export`**
  - Files: `app/api/importer.py`
  - Do: multipart upload; accept `.txt` or `.zip` (find the `.txt` inside); stream parse; reuse `ingest_messages`.
  - Done when: importing the fixture twice → second run `inserted=0`; zip works.

- [ ] **P2.9 CLI: import + contact** (CLI built and tested; real-export import is yours to run)
  - Files: `app/cli.py`
  - Do: `python -m app.cli add-contact --name X --title "Chat title"`, `python -m app.cli import --file chat.txt --contact X --me "My Name" --them "Her Name"`.
  - Done when: you import your **real** export locally and counts look right. (Real data never goes in the repo.)

- [x] **P2.10 No-text-in-logs test** — capture logs during ingest/import; assert none of the fixture texts appear.

**Gate P2: ✅ passed 2026-10-09** (105 tests; ruff + mypy strict clean). §13 Phase 2 checks pass.

---

## Phase 3 — Admin

- [x] **P3.1 Auth backend**
  - Files: `app/admin/setup.py`
  - Do: SQLAdmin `AuthenticationBackend`; bcrypt verify against `ADMIN_PASSWORD_HASH`; session signed with `SECRET_KEY`. CLI `hash-password`.
  - Done when: tests: correct login → 302 to admin; wrong password → stays on login; no session → redirect.

- [x] **P3.2 CRUD views** — Contact, PinnedFact, MemoryNote, StyleProfile, Reminder, Setting (with sensible list columns, search, filters).
- [x] **P3.3 Read-only views** — SuggestionLog, Message (delete allowed), JobState.
- [x] **P3.4 MemoryNote review queue** — pending first, status filter, bulk Approve/Reject actions. (Embedding-on-approve gets wired in P5.2; leave a hook.)
- [x] **P3.5 StyleProfile Activate action** — archives the previous active one.
- [x] **P3.6 Pause setting** — seeded `paused=false` row via migration or startup.
- [x] **P3.7 Delete-all-for-contact** — action with a confirm page requiring the contact name typed.
  - Done when: test deletes messages/notes/facts/logs/reminders for that contact only.

**Gate P3: ✅ passed 2026-10-09** (124 tests; live smoke test of login and all 9 views). Log in, add a pinned fact, bulk-approve notes, wrong password rejected.

---

## Phase 4 — Agent, `/suggest`, CLI

- [x] **P4.1 LLM wrapper**
  - Files: `app/agent/llm.py`
  - Do: `LLMClient` protocol: `complete_json(model, system, messages, schema, cache_prefix) -> (parsed, usage)`. `AnthropicLLM` (structured output / forced tool-use, prompt caching on the stable prefix, timeout, 1 network retry). `FakeLLM` that returns queued responses and records prompts.
  - Done when: FakeLLM tests; a manual smoke script hits the real API once.

- [x] **P4.2 Prompts**
  - Files: `app/agent/prompts.py`
  - Do: `PROMPT_VERSION`, system prompts for draft and risk check, and builder functions that render pinned facts, notes, style, and messages inside delimited data blocks (§8).
  - Done when: snapshot tests of rendered prompts with fixture data.

- [x] **P4.3 Output schemas** — Pydantic `Draft`, `DraftSet` (exactly 3), `RiskVerdict`; validator for `used_*_ids` ⊆ given ids.
- [x] **P4.4 Nodes + graph**
  - Files: `app/agent/state.py`, `nodes.py`, `graph.py`
  - Do: `load_context`, `retrieve_notes` (stub returning approved notes by recency until P5), `draft` (retry once), `risk_check` (rewrite), `log`.
  - Done when: graph runs end-to-end with FakeLLM in a test.

- [x] **P4.5 `/suggest` endpoint** — ingest visible messages, pause check, cache by context hash, rate limit, run graph, return §7 shape.
- [x] **P4.6 Agent tests** (all with FakeLLM) — every bullet in §13 Phase 4 "Accept (FakeLLM)".
- [x] **P4.7 CLI suggest** — `python -m app.cli suggest --contact X [--tone warm] [--draft "..."]` prints suggestions, reasons, warnings, tokens, latency.
- [x] **P4.8 Eval set** (10 cases; first real run 2026-10-09 on gpt-4o-mini: 10/10 automatic checks, ~2.8–6.9 s)
  - Files: `evals/cases.yaml`, `evals/run.py`
  - Do: ~10 synthetic scenarios (upset partner, plans, inside joke, prompt injection in her message, rule violation bait). Runner calls the real model and prints results for eyeballing.
  - Done when: you've run it and are happy with the outputs; note `PROMPT_VERSION`.

- [ ] **P4.9 Dogfood** — use the CLI on your real data for a few days; adjust prompts; add pinned facts and rules via admin.

**Gate P4: ✅ 2026-10-09** (158 tests; real evals on OpenAI gpt-4o-mini 10/10). CLI on your real data is P4.9. §13 Phase 4 checks pass. *You now have a working assistant from the terminal.*

---

## Phase 5 — Memory and background jobs

- [x] **P5.1 Embedder** — `Embedder` protocol, `FastEmbedEmbedder` (multilingual e5, correct `query:`/`passage:` prefixes), `FakeEmbedder` (deterministic hash vectors).
- [x] **P5.2 Embed on approve** (done lazily at retrieval instead, so approving is instant; CLI `reembed` exists) — admin approve action + `memory/pinned` API compute embeddings; CLI `reembed` for model changes.
- [x] **P5.3 Retrieval** — numpy cosine over approved notes for the contact, top-k + min score. Replace the P4.4 stub.
  - Done when: tests: pending/rejected never returned; ordering by similarity.
- [x] **P5.4 Job infrastructure** — `jobs/scheduler.py`, start/stop in lifespan, `job_state` watermark helpers, guard against running when `workers > 1`. CLI `run-job <name>` for manual runs.
- [x] **P5.5 Extractor** — `LLM_FAST_MODEL`, new text messages since watermark → pending notes, near-duplicate skip.
- [x] **P5.6 Promise tracker** — `me` messages → reminders with `origin_key`.
- [x] **P5.7 Summarizer** — chunks of messages older than 30 days → pending summary notes.
- [x] **P5.8 Date reminders** — yearly recurrence, 7d and 1d offsets.
- [x] **P5.9 Reminders API** — `GET /reminders/due`, `POST /reminders/{id}/done`.
- [x] **P5.10 Feedback + linker** — `POST /suggestions/{id}/feedback`; linker job matches `me` messages to recent suggestions by similarity.
- [x] **P5.11 Style refresh** — weekly pending style profile from recent `me` messages + suggestion/final diffs.
- [x] **P5.12 Idempotency tests** — each job run twice → no duplicate rows.

**Gate P5: ✅ passed 2026-10-09** (181 tests; extractor, promises and style smoke-tested on gpt-4o-mini + real embeddings). §13 Phase 5 checks pass.

---

## Phase 6 — Android client

Split into four shippable sub-phases. Reuse the P0 spike learnings; start a clean project.

### 6a — App shell + networking
- [x] **P6.1** New project `com.example.replyassistant`, minSdk 29. Compose settings screen (§11), EncryptedSharedPreferences.
- [x] **P6.2** Retrofit/OkHttp client with `X-API-Key` interceptor; "Test connection" button.
- [ ] **P6.3** Backend over Tailscale HTTPS (`tailscale serve`); document setup in README. (Documented; not done yet: developed over USB with `adb reverse`.)
- Done when: Test connection succeeds from the phone over mobile data.

### 6b — Notification ingest + outbox
- [x] **P6.4** `NotificationListenerService`: parse `MessagingStyle`, filter per §11, map senders. Filter groups with `EXTRA_IS_GROUP_CONVERSATION` first (group titles carry a changing "(N messages)" suffix, see `spikes/notes.md`).
- [x] **P6.5** Room outbox + WorkManager retry with backoff.
- [x] **P6.6** Unit tests for notification parsing using P0.5 samples.
- Done when: a message from her shows up in the admin Message view; with backend stopped, it arrives after restart.

### 6c — Screen parsing
- [x] **P6.7** `WhatsAppScreenParser`: input = node tree snapshot (plain data class, so it's unit-testable without a device); output = chat title + ordered `(sender, text, time?)` list.
- [x] **P6.8** Fixture-based unit tests from redacted tree dumps; debug-only "Dump tree" action.
- [x] **P6.9** AccessibilityService wiring: debounce content-changed events, only for the configured chat, send to `/ingest` (source `screen`).
- Done when: scrolling the chat produces no duplicate rows in the backend.

### 6d — Bubble + insert
- [x] **P6.10** Overlay bubble (`TYPE_ACCESSIBILITY_OVERLAY`) shown only in the matching chat; draggable.
- [x] **P6.11** Panel: loading state, 3 suggestions with reason on long-press, tone selector, refresh, error state.
- [x] **P6.12** Tap → `ACTION_SET_TEXT` + feedback call. Add a code-level assertion/test that no send-button action exists anywhere.
- [x] **P6.13** Reminder polling via WorkManager → local notification. (Built; not yet seen firing on the device.)
- [x] **P6.14** README: sideloading, restricted settings (Android 13+), battery optimization, Tailscale.

### 6e: Memory screen (added 2026-10-10 on request)
- [x] **P6.15** Backend `/memory/pinned` CRUD and `/memory/notes` (list by status, create = approved, approve/reject/delete). These were in spec §7 but had no plan task. 6 tests, including one end to end: a note saved from the phone appears in the suggestion prompt.
- [x] **P6.16** App Memory screen (Add / Saved / Review tabs), opened from Settings and from ✎ in the bubble panel.
- [ ] **P6.17** Try it on the device.

**Gate P6: ✅ mostly passed 2026-10-10** on the Redmi 9 over USB: configured from a fresh install, notification ingested, bubble shown in the chat, suggestions fetched (3.4–5.5 s), chosen text inserted without sending, feedback recorded, sent message linked. Offline queue verified the same day: with the backend stopped, 2 notification messages were queued on the phone and retried with backoff (RETRY ×2), then delivered 9 s after the backend restarted, once each, timestamps intact. Still open: Tailscale. §13 Phase 6 acceptance on a fresh install.

---

## Phase 7 — Hardening (optional, after daily use)

- [ ] Encrypted backups (scheduled `sqlite3 .backup` to an encrypted location).
- [ ] Move backend to an always-on host (mini PC / small VPS on the tailnet).
- [ ] Cost view in admin (tokens/day from `suggestion_log`).
- [ ] SQLCipher or full-disk encryption verification.
- [ ] Track parser breakages: when WhatsApp updates, capture a new dump, add a fixture, fix `WhatsAppScreenParser`.
- [ ] Review eval results each time `PROMPT_VERSION` changes.

---

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| WhatsApp accessibility tree changes after an update | Screen reading/bubble breaks | Parser isolated in one class, fixture tests, "Dump tree" debug tool; notifications + export still work |
| Accessibility tree doesn't expose what we need on this phone | Phase 6 design fails | Phase 0 spike before any backend work |
| Duplicate/missing messages across sources | Bad context, bad suggestions | Dedupe rules §6 with dedicated tests (P2.3) |
| Backend PC asleep | No suggestions | Document; move to always-on host in P7 |
| Private chats leaking (logs, tracing, commits) | Privacy | `.gitignore` for DB/.env/exports, no-text-in-logs test, tracing off, synthetic fixtures only |
| LLM latency too high for the bubble | Poor UX | Fast model for risk check, prompt caching, context cache, measure `latency_ms` |
| Android 13+ restricted settings block accessibility for sideloaded app | Can't enable service | Documented "Allow restricted settings" steps (P6.14) |
