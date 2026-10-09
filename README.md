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
| P3 Admin (SQLAdmin) | next |
| P4 Agent · P5 Memory jobs · P6 Android app | not started |

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
- **`tzdata`** added as a dependency: Windows has no system timezone database, and timezones are needed to convert export times.
- **Starlette TestClient** warns that `httpx` should be replaced by `httpx2`. The warning is silenced in pytest config for now; revisit before it becomes an error.
