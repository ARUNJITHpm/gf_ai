# WhatsApp Reply Assistant

A personal assistant that suggests WhatsApp replies in my voice. It reads the chat, remembers facts I approve, and shows 2–3 suggestions in a floating bubble on Android. **It never sends anything; I always review and send myself.**

- Spec: [`project.md`](project.md)
- Plan and progress: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
- Android feasibility results: [`spikes/notes.md`](spikes/notes.md)

> **Privacy note:** the other person's messages are sent to an AI service (Anthropic) to produce suggestions. Tell her that this assistant exists and what it does.

## Status

| Phase | State |
|---|---|
| P0 Setup + Android spike | ✅ done |
| P1 Backend skeleton | ✅ done |
| P2 Ingest + WhatsApp export import | ✅ done (real-data import is up to you, see below) |
| P3 Admin (SQLAdmin) | ✅ done (set your admin password, see below) |
| P4 Agent + `/suggest` + CLI | ✅ built and tested with a fake model; real-model run needs your API key |
| P5 Memory jobs | next |
| P6 Android app | not started |

## Backend

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). All commands run from `backend/`.

### First-time setup

```bash
cd backend
uv sync                          # creates .venv and installs dependencies
cp .env.example .env             # then fill in API_KEY (see the comment in the file)
uv run alembic upgrade head      # creates the database
```

The database lives outside the repo by default:
- Windows: `%LOCALAPPDATA%\reply-assistant\data.db`
- Linux/macOS: `~/.local/share/reply-assistant/data.db`

Set `DATABASE_URL` in `.env` to put it somewhere else.

### Run

```bash
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

- `GET http://127.0.0.1:8000/health` → `{"status":"ok","db":"ok",...}` (no key needed)
- Every other route needs the header `X-API-Key: <API_KEY from .env>`. `GET /auth/check` is a quick way to test the key.
- API docs: http://127.0.0.1:8000/docs

Always run with a **single worker** (the default). Background jobs, added in Phase 5, run inside the process.

### Admin page

One-time setup: choose a password and put its hash in `.env`.

```bash
uv run python -m app.cli hash-password     # asks twice, prints ADMIN_PASSWORD_HASH='...'
```

Paste that line (with the single quotes) into `backend/.env`. `SECRET_KEY` must be set too; it already is if `.env` was created by the setup. Restart the server and open http://127.0.0.1:8000/admin (user `admin` unless `ADMIN_USER` says otherwise).

- **Memory notes** opens on the *pending* review queue. Tick notes and use **Actions → Approve / Reject**.
- **Pinned facts**: hand-written facts the assistant always sees. Category `rule` facts are hard constraints.
- **Settings**: `paused` (true stops all suggestions), `default_tone`, `recent_messages_n`, `notes_top_k`, `suggest_cache_seconds`. Values are JSON: `true`, `25`, `"warm"`.
- **Style profiles**: select one → **Actions → Activate** (the previous active one is archived).
- **Contacts** → open one → **Delete all data for contact**. You must type the name to confirm. The contact row stays.
- Sessions last 12 hours; 5 login attempts per minute.

If `ADMIN_PASSWORD_HASH` or `SECRET_KEY` is missing, `/admin` simply isn't mounted.

### Reply suggestions

Put your key in `backend/.env`: `ANTHROPIC_API_KEY=sk-ant-...`. Then, from the terminal:

```bash
uv run python -m app.cli suggest --contact "Her name"
uv run python -m app.cli suggest --contact "Her name" --tone playful
uv run python -m app.cli suggest --contact "Her name" --draft "sry cant come today"
```

Over HTTP: `POST /suggest` with `{"contact_hint": "<chat title>", "visible_messages": [...], "tone": "...", "draft_text": "..."}`; see `/docs`.

How it works: the last 25 messages, all active pinned facts, up to 5 approved notes and the active style profile go to `LLM_MODEL` (Sonnet 5.5), which drafts 3 replies. `LLM_FAST_MODEL` (Haiku 5.5) then checks them for rudeness, broken rules and invented facts, and rewrites or removes the bad ones. Each call is logged in the admin under **Suggestion logs** (tokens, latency, prompt version). The same request within 60 s returns the cached result.

**Evals:** `uv run python -m evals.run` runs 10 synthetic scenarios (upset partner, prompt injection, rule bait, Manglish, …) against the real model and prints the replies for you to read. It costs a few cents. Run it again after any change to `app/agent/prompts.py`.

### Import your chat history

On the phone: open the chat → ⋮ → More → **Export chat** → **Without media**, and send the file to your PC. Then:

```bash
uv run python -m app.cli add-contact --name "Her name" --title "Chat title as WhatsApp shows it"
uv run python -m app.cli import --file "WhatsApp Chat with ....zip" --contact "Her name" --me "Your name in the export" --them "Her name in the export"
uv run python -m app.cli list-contacts
```

- `.txt` and the `.zip` WhatsApp creates both work. Re-importing is safe: stored messages are skipped.
- If the names are wrong, the error lists the author names found in the file.
- If every day in the file is 12 or less, add `--date-order dmy` (India) or `mdy`.
- Keep exports out of the repo. `exports/` is git-ignored if you want a place for them.

Over HTTP the same import is `POST /import/whatsapp-export` (multipart; see `/docs`).

### Checks

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy            # types (strict)
```

### Database migrations

After changing a model:

```bash
uv run alembic revision --autogenerate -m "describe the change"
# review the generated file in alembic/versions/, then:
uv run alembic upgrade head
```

`tests/test_migrations.py` fails if models and migrations drift apart.

## Decisions

Choices made where the spec was open. See also `project.md` §15.

- **Repo stays in the current (OneDrive) folder.** The database defaults to `%LOCALAPPDATA%` so the live SQLite file isn't synced.
- **App factory:** run uvicorn with `app.main:create_app --factory`, so importing the app never needs a `.env` (keeps tests isolated).
- **Public routes:** `/health` plus the OpenAPI docs (`/docs`, `/openapi.json`), which contain no data. Everything else sits on a router that requires `X-API-Key`; a test fails if a route is added outside it. Unknown paths return 404.
- **`GET /auth/check`:** added for the Android "Test connection" button.
- **Datetimes:** a `UTCDateTime` column type stores naive UTC and returns aware UTC. Naive datetimes are rejected on save, so unconverted local times can't slip in.
- **Enums** are stored as their string values with CHECK constraints.
- **Migrations run with SQLite foreign keys OFF.** Batch mode recreates tables, and with FKs on, dropping a parent table would cascade-delete child rows. A `foreign_key_check` runs after each migration.
- **Logging:** use `log_event(name, **ids_and_counts)`. It refuses fields such as `text`/`content` and long strings, so message text can't reach the logs.
- **API key** must be at least 16 characters.
- **Export system lines** (encryption notice, number changes, …) are counted and skipped, not stored. They have no sender, and the `message` table requires one.
- **Dedupe trade-off for screen reads:** when visible messages don't line up with the last 100 stored ones (scrolled far back), only texts never seen before are inserted. A genuinely new repeat of an old text such as "ok" can be missed there; notifications usually catch it.
- **Media detection** uses exact placeholders ("<Media omitted>", "image omitted", "Sticker") and a few emoji prefixes ("📷 Photo", "🎤 Voice message"); see `MEDIA_PLACEHOLDERS` in `app/services/ingest.py`.
- **Unknown contact on `/ingest`** → 404; contacts are never auto-created.
- **`/ingest` limits:** at most 500 messages per call, 20,000 characters per message, 120 calls per minute.
- **Imports**: up to 200 MB of chat text; inside a `.zip`, the file named "WhatsApp Chat…" is used (otherwise the largest `.txt`). 50,000 messages import in about 12 s, and a re-import takes about 5 s.
- **Admin auth is separate from the API key.** `/admin` uses its own login and session cookie (HttpOnly, SameSite=Lax, 12 h; `ADMIN_HTTPS_ONLY=true` adds Secure). SQLAdmin actions are GET links. SameSite=Lax blocks cross-site *embedded* requests, and the admin is only reachable on localhost or the tailnet, so no extra CSRF tokens were added.
- **Admin password** check always runs bcrypt, even for a wrong username, so timing doesn't reveal valid usernames.
- **Models have a `contact` relationship** so admin forms show a contact dropdown. The schema is unchanged.
- **SQL parameters are hidden** in SQLAlchemy errors (`hide_parameters=True`), so a failed insert can't put message text into a traceback or log.
- **Timezone stays in `.env`**, not in the settings table (it's needed at import time and rarely changes). The table holds runtime switches only; known keys and types live in `app/services/settings_store.py` and are validated on save.
- **"Delete all data for contact"** deletes messages, notes, facts, style profiles, suggestion logs and reminders, but keeps the contact so the phone can keep sending. To remove the contact too, delete it normally (the database cascades).
- **Suggestions:** Sonnet 5.5 drafts at effort `low` (chat replies don't benefit from deep reasoning, and it keeps latency down); Haiku 5.5 does the risk check, also at `low`. Both use structured outputs validated with Pydantic. The rules prompt plus contact, facts and style form a stable, cached prefix; messages and notes come after it.
- **Refusals:** the Sonnet call has server-side refusal fallback on (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). If the model still declines, `/suggest` returns no suggestions and a warning instead of an error.
- **Risk check rewrites instead of dropping** a flagged reply; it drops one only if the checker gives no rewrite. If the risk check itself fails, the drafts are returned with a "read carefully" warning.
- **Errors:** invalid output twice → 502; Anthropic API unreachable or erroring → 503. Neither stores a log row.
- **Prompt-injection defence:** chat text only appears inside `<messages>`/`<notes>`/`<draft>` blocks, with `<`/`>` replaced by `‹`/`›` so a message can't close a block. Tests check placement. Whether the model *behaves* correctly is checked by `evals/` with the real model.
- **Note retrieval** in Phase 4 returns the most recently updated approved notes; Phase 5 switches it to embedding similarity.
- **LangSmith tracing** is forced off in code (`app/agent/llm.py`) because LangGraph installs it.
- **Rate limiter buckets are per app instance** (and per client IP), so separate apps don't share limits.
- **`greenlet`** (via `sqlalchemy[asyncio]`) is installed because SQLAdmin imports SQLAlchemy's async module even when used with sync sessions.
- **`tzdata`** added as a dependency: Windows has no system timezone database, and timezones are needed to convert export times.
- **`httpx2`** arrives with the Anthropic SDK 1.x, and Starlette's TestClient uses it, so the old `httpx` deprecation warning is gone (no filter needed).
