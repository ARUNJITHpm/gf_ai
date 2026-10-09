# WhatsApp Reply Assistant

A personal assistant that suggests WhatsApp replies in my voice. It reads the chat, remembers facts I approve, and shows 2–3 suggestions in a floating bubble on Android. **It never sends anything; I always review and send myself.**

- Spec: [`project.md`](project.md)
- Plan and progress: [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
- Android feasibility results: [`spikes/notes.md`](spikes/notes.md)

> **Privacy note:** the other person's messages are sent to an AI service (OpenAI or Anthropic, whichever `LLM_PROVIDER` selects) to produce suggestions. Tell her that this assistant exists and what it does.

## Status

| Phase | State |
|---|---|
| P0 Setup + Android spike | ✅ done |
| P1 Backend skeleton | ✅ done |
| P2 Ingest + WhatsApp export import | ✅ done (real-data import is up to you, see below) |
| P3 Admin (SQLAdmin) | ✅ done (set your admin password, see below) |
| P4 Agent + `/suggest` + CLI | ✅ done; running on OpenAI `gpt-4o-mini`, evals 10/10 |
| P5 Memory jobs | ✅ done |
| P6 Android app | ✅ done on a Redmi 9 over USB (incl. offline queue); Tailscale still open |

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

The provider is chosen in `backend/.env`. Current setup:

```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
LLM_FAST_MODEL=gpt-4o-mini
```

To use Claude instead: `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...`, `LLM_MODEL=claude-sonnet-5-5`, `LLM_FAST_MODEL=claude-haiku-5-5`. Then, from the terminal:

```bash
uv run python -m app.cli suggest --contact "Her name"
uv run python -m app.cli suggest --contact "Her name" --tone playful
uv run python -m app.cli suggest --contact "Her name" --draft "sry cant come today"
```

Over HTTP: `POST /suggest` with `{"contact_hint": "<chat title>", "visible_messages": [...], "tone": "...", "draft_text": "..."}`; see `/docs`.

How it works: the last 25 messages, all active pinned facts, up to 5 approved notes and the active style profile go to `LLM_MODEL`, which drafts 3 replies. `LLM_FAST_MODEL` then checks them for rudeness, broken rules and invented facts, and rewrites or removes the bad ones. Each call is logged in the admin under **Suggestion logs** (tokens, latency, prompt version). The same request within 60 s returns the cached result.

**Evals:** `uv run python -m evals.run` runs 10 synthetic scenarios (upset partner, prompt injection, rule bait, Manglish, …) against the real model and prints the replies for you to read. It costs a few cents. Run it again after any change to `app/agent/prompts.py`.

### Memory and background jobs

While the server runs, these jobs run in the background. Each records its last run and result in the admin under **Job states**.

| Job | Every | What it does |
|---|---|---|
| `linker` | 5 min | Matches messages you sent to the suggestion they came from (fills `final_text`) |
| `extractor` | 15 min | Proposes durable facts from new messages as **pending** notes (when 50+ new messages are waiting, or one has waited a day) |
| `promises` | 15 min | Turns your recent commitments ("I'll call you tonight") into reminders |
| `date_reminders` | daily | Reminders 7 days and 1 day before pinned dates (09:00 local) |
| `summarizer` | daily | Rolls chat older than 30 days into weekly **pending** summary notes |
| `style` | weekly | Drafts a **pending** style profile once you've sent 20+ new messages |

Nothing becomes active by itself. Approve notes in **Memory notes**, and activate style profiles in **Style profiles → Actions → Activate**.

- Run a job now: `uv run python -m app.cli run-job extractor`, or `--all`.
- After changing `EMBEDDING_MODEL`: `uv run python -m app.cli reembed`.
- `ENABLE_JOBS=false` in `.env` turns the jobs off.
- After importing a long history, expect the first extractor and summarizer runs to take a while and to fill the review queue (roughly one model call per 80 messages, or per week of old chat). With gpt-4o-mini that costs cents.

**Endpoints for the phone:** `POST /suggestions/{id}/feedback` `{"chosen_index": 1}`, `GET /reminders/due?within_hours=24`, `POST /reminders/{id}/done`.

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

## Android app

Thin client in `android/`. It reads the configured WhatsApp chat (notifications + the open screen), shows a 💬 bubble with suggestions, and types the chosen one into the message box. **It never presses Send.** A unit test fails the build if any click/gesture action appears in the code.

### Build

Requires JDK 17 and the Android SDK (`ANDROID_HOME`). From `android/`:

```bash
./gradlew testDebugUnitTest assembleDebug     # APK: app/build/outputs/apk/debug/app-debug.apk
```

### Install (sideload)

- Phone: enable USB debugging (Developer options).
- `adb install -r app/build/outputs/apk/debug/app-debug.apk`
  - **Xiaomi/MIUI** blocks this (`INSTALL_FAILED_USER_RESTRICTED`) unless "Install via USB" is on (needs a Mi account). Workaround: `adb push app-debug.apk /sdcard/Download/` (Git Bash: prefix with `MSYS_NO_PATHCONV=1`) and install it from File Manager.
- **Android 13+:** if the accessibility switch is greyed out ("Restricted setting"), go to Settings → Apps → Reply Assistant → ⋮ → **Allow restricted settings**.
- **Battery:** set the app to **No restrictions** / disable battery optimization, and on MIUI turn **Autostart** on, or the system kills the services.

### Set up

1. Settings → Accessibility → Downloaded apps → **Reply Assistant (WhatsApp)** → ON.
2. Notification access → **Reply Assistant** → Allow. (`adb shell cmd notification allow_listener com.example.replyassistant/com.example.replyassistant.notify.WaNotificationListener` also works.)
3. Backend: `uv run python -m app.cli add-contact --name "<name>" --title "<chat title as WhatsApp shows it>"`.
4. Open the app: backend URL, API key, chat title → **Save** → **Test connection**.
   - Debug builds can be prefilled from the PC so the long key needn't be typed (you still tap Save):
     `adb shell am start -n com.example.replyassistant/.ui.SettingsActivity --es backend_url http://127.0.0.1:8000 --es api_key <API_KEY> --es chat_title "'<title>'"`

### Connecting the phone to the backend

- **USB (development):** run the backend on the PC, then `adb reverse tcp:8000 tcp:8000`; the app uses `http://127.0.0.1:8000`. The tunnel disappears whenever USB reconnects. Re-run it, or keep a loop going:
  `while true; do adb wait-for-device; adb reverse --list | grep -q tcp:8000 || adb reverse tcp:8000 tcp:8000; sleep 3; done`
- **Anywhere (Tailscale):** install Tailscale on PC and phone and sign in to the same tailnet. On the PC: `tailscale serve --bg 8000`, which gives `https://<pc-name>.<tailnet>.ts.net`. Put that URL in the app. Plain HTTP is only allowed to `127.0.0.1`/`localhost` (`res/xml/network_security_config.xml`), so use the HTTPS URL.
- If the backend is unreachable, messages wait in the phone's outbox (Room) and are delivered later. Settings shows how many are waiting.

### Memory screen (notes and facts about her)

Open it from Settings → **Memory**, or tap **✎** in the 💬 panel while chatting.

| Type | Example | How it's used |
|---|---|---|
| **Note** | "She's stressed about the move; I want to be more patient" | Used only when the chat is related. Write as many as you like. |
| **Fact** / **Situation** / **Inside joke** | "She's vegetarian" / "We're long distance until March" / "potato" | Always sent to the model. Keep them few. |
| **Rule** | "Never joke about her weight" | Always sent; the model must not break it, and the risk check enforces it |
| **Date** | Birthday 17 Oct, every year | Always sent, plus reminders 7 days and 1 day before |

- **Saved** lists everything. Facts can be paused (switch) or deleted; notes can be deleted.
- **Review** shows notes the assistant proposed from your chats; nothing is used until you approve it.
- Notes you write yourself are approved immediately.
- API: `GET/POST /memory/pinned`, `PATCH/DELETE /memory/pinned/{id}`, `GET/POST /memory/notes?status=`, `POST /memory/notes/{id}/approve|reject`, `DELETE /memory/notes/{id}`.

### When WhatsApp updates and the bubble stops working

All WhatsApp-specific view ids live in `WhatsAppScreenParser.kt`. In the app tap **Debug: dump next chat screen**, then open the chat. A redacted tree (text replaced by lengths) is saved under the app's files (`adb shell run-as com.example.replyassistant ls files/dumps`). Turn it into a test fixture like `src/test/resources/screen_wa_2_26_39.json` (with made-up text) and fix the ids.

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
- **LLM provider is switchable** (`LLM_PROVIDER=openai|anthropic`). Both clients sit behind the same `LLMClient` interface (`app/agent/llm.py`) and use structured outputs validated with Pydantic. Currently OpenAI `gpt-4o-mini` for both drafting and the risk check. OpenAI caches long identical prompt prefixes automatically; `effort` applies only to Claude.
- **Suggestions on Claude:** Sonnet 5.5 drafts at effort `low` (chat replies don't benefit from deep reasoning, and it keeps latency down); Haiku 5.5 does the risk check, also at `low`. Both use structured outputs validated with Pydantic. The rules prompt plus contact, facts and style form a stable, cached prefix; messages and notes come after it.
- **Refusals:** an OpenAI `refusal` or a Claude `refusal` stop reason gives no suggestions plus a warning. On Claude, the Sonnet call also has server-side refusal fallback on (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). If the model still declines, `/suggest` returns no suggestions and a warning instead of an error.
- **Risk check rewrites instead of dropping** a flagged reply; it drops one only if the checker gives no rewrite. If the risk check itself fails, the drafts are returned with a "read carefully" warning.
- **Errors:** invalid output twice → 502; Anthropic API unreachable or erroring → 503. Neither stores a log row.
- **Prompt-injection defence:** chat text only appears inside `<messages>`/`<notes>`/`<draft>` blocks, with `<`/`>` replaced by `‹`/`›` so a message can't close a block. Tests check placement. Whether the model *behaves* correctly is checked by `evals/` with the real model.
- **Note retrieval** in Phase 4 returns the most recently updated approved notes; Phase 5 switches it to embedding similarity.
- **LangSmith tracing** is forced off in code (`app/agent/llm.py`) because LangGraph installs it.
- **Rate limiter buckets are per app instance** (and per client IP), so separate apps don't share limits.
- **Embedding model:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (220 MB, local). The spec's `multilingual-e5-small` isn't available in fastembed, and `multilingual-e5-large` is 2.2 GB. It is good for English; on a quick check it was weaker for Manglish. Models are cached in the data folder (`%LOCALAPPDATA%\reply-assistant\models`), not in Temp.
- **Note retrieval:** cosine similarity between the contact's last 3 messages and approved notes; top `notes_top_k` with score ≥ `notes_min_score` (setting, default 0.2). Notes are embedded lazily on first use, so approving in the admin is instant, and they are re-embedded automatically when the model changes.
- **Jobs run in-process** (APScheduler) and are guarded by an OS file lock (`jobs.lock` in the data folder), so with several server processes only one runs jobs. Every job is idempotent: watermarks are saved in the same transaction as the job's output, and reminders use unique `origin_key`s. Job failures are recorded as `error: <Type>` and never crash the app.
- **Promises** only come from your messages of the last 3 days, so an imported history doesn't create hundreds of overdue reminders. A promise with no time, or a time before the message, is due 3 hours after it.
- **The summarizer** tracks both a time cursor and a message-id cursor (`job_state.cursor_at`, migration 0002), so old messages imported later still get summarized.
- **The style refresh** uses `LLM_MODEL` (quality) and the other jobs use `LLM_FAST_MODEL`. Examples in a style profile are picked by index from your real messages, so they're always verbatim.
- **Android Kotlin version:** AGP 9.3.3 compiles Kotlin itself (bundled 2.2.10). Kotlin 2.4.21 is pinned through the Compose and serialization plugins, because the Compose compiler must equal the Kotlin compiler; KSP 2.3.12 for Room.
- **API key on the phone:** encrypted with an AES-GCM key in the Android Keystore. `EncryptedSharedPreferences` is deprecated, so this is done directly. Same protection.
- **Bubble and panel are plain Views** on a `TYPE_ACCESSIBILITY_OVERLAY` window. Compose inside an accessibility service overlay needs lifecycle workarounds, so Compose is used for the settings screen only.
- **Every ingest goes through the Room outbox** (not only when offline), so there is one code path; WorkManager delivers within seconds. 400-level answers other than 401/429 (unknown contact, bad payload) drop the item instead of retrying forever.
- **Screen reads send no timestamps**; the backend aligns them with the stored chat. The parser does read the bubble time (`9:41 pm`), but combining it with date dividers reliably is left for later.
- **Dedupe fix from the device test:** a chat opened 38 minutes after a message arrived (screen copy stamped "now"), followed by the notification with the real time, stored the message twice. A timeless screen row now matches a timed copy if first seen up to 2 days after sending (newest identical copy wins). Regression tests in `tests/test_ingest_dedupe.py`.
- **Debug-only settings prefill** via `adb shell am start ... --es api_key ...`. Ignored in release builds, because any app could start the activity and point it at another server.
- **`greenlet`** (via `sqlalchemy[asyncio]`) is installed because SQLAdmin imports SQLAlchemy's async module even when used with sync sessions.
- **`tzdata`** added as a dependency: Windows has no system timezone database, and timezones are needed to convert export times.
- **`httpx2`** arrives with the Anthropic SDK 1.x, and Starlette's TestClient uses it, so the old `httpx` deprecation warning is gone (no filter needed).
