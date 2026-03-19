# flight-deals — CLAUDE.md

Guidelines for any Claude model working on this project.

---

## What this project is

A persistent async Telegram bot that finds cheap flights via the Aviasales/Travelpayouts API
and notifies registered users. Runs as a webhook-based aiohttp daemon on the VPS.

- **Single source file:** `main.py` (~1000 lines)
- **One dependency:** `aiohttp >= 3.9`
- **State:** JSON file at `/app/data/state.json` (inside Docker)
- **Current version:** 1.1

---

## Versioning policy

- **Feature releases:** 1.2, 1.3, 1.4 … (new commands, new search modes, etc.)
- **Bug fixes:** 1.1.1, 1.1.2 … (fixes on the current release without new features)
- After every change, update `__version__` in `main.py` and add an entry to `CHANGELOG.md`.
- Tag every release on `main`: `git tag v1.2`

---

## Git workflow

1. **Never commit directly to `main`.** Always work on a branch named after what you're doing:
   - Feature: `feature/short-description`
   - Bug fix: `fix/short-description`
2. Fix and commit on the branch, then merge to `main` with `--no-ff`.
3. Tag `main` after merging.
4. **Never push** unless the user explicitly asks.
5. **Never commit `config.json`** — it contains live tokens. `.gitignore` covers it, but double-check.

---

## Running tests

```bash
# First time only — create venv and install dependencies
python3 -m venv .venv
.venv/bin/pip install pytest aiohttp

# Run unit tests (no network, no Docker needed)
.venv/bin/pytest tests/test_logic.py -v
```

**Run tests before committing any change** that touches:
- `deal_hash`, `max_price_for_duration`, `filter_deals`, `format_deal`

If you add a new pure function, add tests for it in `tests/test_logic.py`.

---

## Deploying

This service is built and started via the **shared** docker-compose in `/opt/comparity`.

```bash
# Rebuild and restart only this service
cd /opt/comparity && docker compose up -d --build backend-flightdeals

# View logs
docker logs comparity-backend-flightdeals-1 --tail=100 -f
```

Do **not** run a standalone `docker build` or `docker run` — the container must be on the
`comparity_default` network or Telegram webhooks will silently fail.

---

## Configuration

Config is loaded from `config.json` + environment variables (env vars take priority).
The source of truth for all secrets is `/opt/comparity/.env`.
Do **not** create a separate `.env` inside `/opt/flight-deals/`.

Key config values and what they do:

| Key | Description |
|---|---|
| `aviasales_token` | Travelpayouts API token |
| `telegram_bot_token` | Bot token from @BotFather |
| `admin_chat_id` | Telegram chat ID of the admin user |
| `webhook_host` | Public HTTPS URL (e.g. `https://yourdomain.com`) |
| `webhook_port` | Port in the webhook URL sent to Telegram — must be 443, 80, 88, or 8443 |
| `listen_port` | Port the aiohttp server actually binds to internally — 8080 |
| `webhook_path` | URL path for the webhook endpoint (e.g. `/webhook-flightdeals`) |

`webhook_port` and `listen_port` are intentionally different — Caddy receives on 443 and proxies to 8080.

---

## Known traps — read before touching anything

### 1. The 60-second interval trap
`hourly_flight_check()` uses `asyncio.sleep(3600)`. In the past this was accidentally left at
`60` during development. At 60s the bot hammers the Aviasales API 1440 times/day and spams users.
**Always verify the sleep value is 3600 before merging.**

### 2. Docker-only paths in module-level code
`main.py` writes to `/app/startup.txt` and `/app/bot.log` at import time.
These paths only exist inside Docker. `tests/conftest.py` patches them so tests work outside Docker.
Do not add more `/app/*` writes at module level — if you must log somewhere, do it inside `main()`.

### 3. State file location
- **Inside Docker:** `/app/data/state.json`
- **Local dev:** the path is determined by `load_state()` — check before assuming
- Do not mount the entire `/app` directory as a Docker volume — it shadows the files installed
  by the Dockerfile (this caused a hard-to-debug port binding failure in the past).

### 4. Webhook port vs listen port
Telegram requires the webhook URL to use port 443 (or 80/88/8443).
The bot internally listens on 8080. Caddy bridges them.
If you see Telegram rejecting the webhook registration, check both values in `config.json`.

### 5. `sent_deals` is a set, not a list
State is serialized to JSON. Sets are not JSON-serializable — `save_state()` converts them.
If you add any new set fields to state, handle serialization the same way.

---

## Architecture at a glance

```
Telegram  →  Caddy (443)  →  aiohttp (8080)  →  handle_webhook()
                                                        ↓
                                               process_single_update()
                                                        ↓
                                               command handlers

asyncio background task:
  hourly_flight_check()  →  search_for_user()  →  fetch_flights()  →  Aviasales API
                                                        ↓
                                               filter_deals()
                                                        ↓
                                               send_tg()  →  Telegram
```

---

## What NOT to do

- Do not add new Python dependencies without a clear reason — the `aiohttp`-only stack is intentional.
- Do not switch to a Telegram library wrapper (like `python-telegram-bot`) — the raw HTTP approach
  is deliberate and simpler for this use case.
- Do not split `main.py` into multiple files unless the user explicitly asks — it's a deliberate
  single-file design for a bot of this size.
- Do not hardcode the webhook host, tokens, or chat IDs anywhere in the source — always read from config.
