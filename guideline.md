# Flight Deals Bot — Refactoring Guideline

## Goal

Refactor the single-file polling bot (`main.py`) into a **webhook-based Telegram bot** that runs as a persistent process on a VPS. The bot must remain a single `main.py` file. The VPS already hosts other bots, so this bot will need its own port for the webhook endpoint.

---

## Current Architecture (for context)

- **Single file:** `main.py` (556 lines)
- **Dependencies:** `requests` only
- **Telegram:** raw HTTP calls, `getUpdates` polling (non-blocking, `timeout=0`)
- **Scheduling:** GitHub Actions cron (every hour) — script runs once and exits
- **State:** `state.json` on disk + mirrored to a GitHub Gist
- **Flight API:** Aviasales/Travelpayouts v3 (`prices_for_dates` endpoint)
- **Flight mode:** one-way only (`"one_way": "true"`)
- **Config:** `config.json` file + env vars override; keys: `aviasales_token`, `telegram_bot_token`, `admin_chat_id`, `gist_id`
- **Admin chat_id:** `1847504`

---

## Target Architecture

```
VPS (persistent process)
│
├── Telegram Webhook (aiohttp server on a dedicated port)
│   └── POST /webhook  ← Telegram pushes updates here
│
├── Hourly flight check (asyncio background task, NOT cron)
│   └── For each user: call Aviasales API, filter deals, notify
│
└── state.json (local file, no more Gist dependency)
```

### Key Principles

1. **Same APIs** — Aviasales/Travelpayouts v3 and Telegram Bot API (raw HTTP, no library wrappers).
2. **Webhooks for Telegram** — commands are processed instantly, no 1-hour delay.
3. **Flight API calls stay hourly** — one scheduled check per hour, iterating all users. We minimize flight API usage.
4. **Single file** — everything stays in `main.py`.
5. **Dependencies:** switch from `requests` to `aiohttp` (needed for the async webhook server). `requirements.txt` becomes: `aiohttp>=3.9`.

---

## New Config

`config.json` shape (env vars still override):

```json
{
  "aviasales_token": "...",
  "telegram_bot_token": "...",
  "admin_chat_id": "1847504",
  "webhook_host": "https://your-vps-domain.com",
  "webhook_port": 8443,
  "webhook_path": "/webhook-flightdeals-SECRET"
}
```

New env vars to support:
- `WEBHOOK_HOST` — public HTTPS URL of the VPS (e.g. `https://vps.example.com`)
- `WEBHOOK_PORT` — port for this bot's webhook listener (pick one not used by the other 2 bots)
- `WEBHOOK_PATH` — URL path with a secret token for security (e.g. `/webhook-flightdeals-a8f3b2`)

On startup, call `setWebhook` to register: `POST /bot{token}/setWebhook` with `url = {webhook_host}:{webhook_port}{webhook_path}`.

Remove all Gist-related code (`gist_id`, `GH_TOKEN`, gist load/save). State lives only in `state.json` on the VPS.

---

## State Changes

### New `state.json` Shape

```json
{
  "users": {
    "<chat_id>": {
      "name": "Display Name",
      "username": "tg_username",
      "referral_answer": "как узнали о боте — ответ пользователя",
      "settings": {
        "origin": "BUD",
        "destination": "",
        "days_ahead": 3,
        "base_price_eur": 20,
        "base_duration_minutes": 90,
        "price_increment_eur": 10,
        "increment_minutes": 30,
        "currency": "eur",
        "market": "hu",
        "limit": 100,
        "direct_only": false
      },
      "sent_deals": ["hash1", "hash2"]
    }
  },
  "pending": {
    "<chat_id>": {
      "name": "...",
      "username": "...",
      "state": "awaiting_referral"
    }
  },
  "revoked": {
    "<chat_id>": { "name": "...", "username": "..." }
  },
  "last_update_id": 0
}
```

Key additions:
- `destination` in settings (empty string = one-way, IATA code = round-trip)
- `referral_answer` in user data
- `pending[].state` for the onboarding conversation flow
- `revoked` dict to track kicked users

---

## Feature 1: Round-Trip (Back-and-Forth) Support

### User Commands

- `/destination XXX` — set destination IATA code, enabling round-trip mode
- `/destination off` or `/destination` with no arg — clear destination, revert to one-way mode

### How It Works

**One-way mode** (destination is empty — current behavior, unchanged):
- API call: `one_way=true`, no destination param
- Price check: `price <= max_price_for_duration(duration)`

**Round-trip mode** (destination is set):
- Make TWO separate API calls per day:
  1. Outbound: `origin=ORIGIN, destination=DEST, departure_at=DATE, one_way=true`
  2. Return: `origin=DEST, destination=ORIGIN, departure_at=DATE, one_way=true`
- **Important:** the Aviasales `prices_for_dates` API doesn't have a native round-trip search that returns segment pairs. So we fetch one-way prices for both directions separately and combine them.
- The user's `/price` setting means **per-segment limit**. But we check **total flexibility**: a pair qualifies if `outbound_price + return_price <= 2 * per_segment_limit`. This way a 29+9 combo passes a 20/segment limit (29+9=38 <= 40).
- Duration thresholds apply per-segment independently (each leg must meet its own duration-based price limit).
- **Pairing logic:** For each day, collect outbound deals and return deals. Find all combinations where `outbound.price + return.price <= 2 * per_segment_limit`. Sort by total price. Report the best combos.
- Deal hash for round-trips: `f"{origin}-{dest}-{outbound_date}-{return_date}-{total_price}"`

### Display Format for Round-Trip Deals

```
✈️ BUD ↔ PRG — 38 EUR total

  → BUD → PRG
    2026-03-04 17:55 | 1h15m | direct
    💰 29 EUR (limit 20 EUR/segment)
    FR 4034

  ← PRG → BUD
    2026-03-04 09:30 | 1h10m | direct
    💰 9 EUR (limit 20 EUR/segment)
    W6 2336

https://www.aviasales.com/...outbound_link
https://www.aviasales.com/...return_link
```

### `/settings` Output Update

Add a line:
```
🔄 Тип: `туда-обратно (PRG)` или `в одну сторону`
```

---

## Feature 2: Webhook-Based Telegram Integration

### Startup Sequence

```python
async def main():
    cfg = load_config()
    state = load_state(cfg)

    # 1. Set up webhook
    await set_webhook(cfg)

    # 2. Start aiohttp web server for incoming updates
    app = web.Application()
    app.router.add_post(cfg["webhook_path"], handle_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", cfg["webhook_port"])
    await site.start()

    # 3. Start background hourly flight check
    asyncio.create_task(hourly_flight_check(cfg, state))

    # 4. Keep running forever
    await asyncio.Event().wait()
```

### Webhook Handler

```python
async def handle_webhook(request):
    data = await request.json()
    # Process the update (same logic as current process_telegram_commands,
    # but handling a single update instead of a batch)
    await process_single_update(data, cfg, state)
    return web.Response(text="ok")
```

### Background Flight Check

```python
async def hourly_flight_check(cfg, state):
    while True:
        print(f"🔍 Hourly check for {len(state['users'])} user(s)…")
        for chat_id in list(state["users"].keys()):
            try:
                await search_for_user(chat_id, state, cfg)
            except Exception as e:
                print(f"  ❌ Error for {chat_id}: {e}")
        save_state(state, cfg)
        await asyncio.sleep(3600)  # 1 hour
```

### Async HTTP Calls

Replace all `requests.get/post` with `aiohttp.ClientSession` calls. Create one session at startup, reuse it. Example:

```python
session = aiohttp.ClientSession()

async def send_tg(text, chat_id, cfg, parse_mode=None):
    bot_token = cfg["telegram_bot_token"]
    text += FOOTER
    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": "Markdown",
    }
    async with session.post(
        f"{TG_API.format(token=bot_token)}/sendMessage",
        json=payload, timeout=aiohttp.ClientTimeout(total=15)
    ) as resp:
        pass
```

---

## Feature 3: `/revoke` Admin Command

### Behavior

```
Admin sends: /revoke 123456
→ Remove user 123456 from state["users"]
→ Add to state["revoked"] = { "123456": { name, username } }
→ Send admin: "✅ Пользователь X удалён."
→ Send user 123456: "⛔ Ваш доступ отозван администратором."
```

If a revoked user sends `/start`, they get: `"⛔ Ваш доступ был отозван."` — they cannot re-apply.

---

## Feature 4: Referral Question on Registration

### Updated `/start` Flow for New Users

```
User sends /start
├── Already approved → show help
├── Already pending (state=awaiting_referral) → "Пожалуйста, ответьте на вопрос выше."
├── Already pending (state=awaiting_approval) → "Запрос отправлен, ожидайте."
├── In revoked list → "⛔ Ваш доступ был отозван."
└── Brand new user →
    ├── Add to pending with state="awaiting_referral"
    └── Send: "Откуда вы узнали про бота? Поделитесь, пожалуйста 🙏"
```

When a user in `pending` with `state=awaiting_referral` sends a **non-command** message:
```
→ Save their answer as pending[chat_id]["referral_answer"]
→ Change state to "awaiting_approval"
→ Send user: "📨 Спасибо! Запрос отправлен. Ожидайте одобрения."
→ Notify admin: "🆕 Новый запрос от *Name* (@username)\nID: `123456`\nОткуда узнал: «их ответ»\n\n/approve 123456 или /reject 123456"
```

When approved, store the referral answer in the user's record:
```python
state["users"][chat_id] = {
    "name": ...,
    "username": ...,
    "referral_answer": pending_info["referral_answer"],
    "settings": {},
    "sent_deals": [],
}
```

---

## Feature 5: `/userlist` Admin Command

### Behavior

```
Admin sends: /userlist
→ Send a message listing ALL user chat_ids, one per line:
```

Format:
```
📋 Все ID пользователей:

1847504
2938475
1029384
5847261

Всего: 4
```

This is different from the existing `/users` command which shows names and settings. `/userlist` is a raw ID dump for admin convenience.

Keep the existing `/users` command as-is (it shows names, settings, pending list).

---

## Feature 6: `/days` — Search Window

The current `/days N` command already exists and sets `days_ahead`. Confirm it works as follows:

- `/days 3` → search 3 days ahead (today, tomorrow, day after)
- `/days 7` → search 7 days ahead
- `/days 90` → search 90 days ahead

This already works in the current code (`range(days_ahead)` loop in `search_for_user`). No change needed except ensuring the help text makes this clear. Update HELP_TEXT to:

```
/days N — окно поиска (сколько дней вперёд, напр. 3, 7, 90)
```

**Important note for large windows:** When `days_ahead` is large (like 90), the bot makes one API call per day per user (or two for round-trip). For 90 days round-trip, that's 180 API calls per user per hour. The Aviasales API has rate limits. Add a small delay between API calls (`await asyncio.sleep(0.3)`) to avoid hitting rate limits. Also cap `days_ahead` at a maximum of 90 with a validation message.

---

## Updated Command Reference

### User Commands

| Command | Description |
|---|---|
| `/start` | Register or show help |
| `/help` | Show help text |
| `/settings` | Show current settings |
| `/origin XXX` | Set departure IATA code |
| `/destination XXX` | Set destination (enables round-trip) |
| `/destination off` | Clear destination (back to one-way) |
| `/days N` | Search window in days (1–90) |
| `/price N` | Base price threshold per segment (EUR) |
| `/duration N` | Max duration for base price (minutes) |
| `/increment N` | Extra EUR per 30 min of flight time |
| `/direct` | Toggle direct-only flights |
| `/reset` | Clear sent deals history |

### Admin Commands

| Command | Description |
|---|---|
| `/approve ID` | Approve a pending user |
| `/reject ID` | Reject a pending user |
| `/revoke ID` | Remove an approved user (permanent ban) |
| `/users` | List users with settings summary |
| `/userlist` | List all user IDs (raw dump) |

---

## Updated HELP_TEXT

```python
HELP_TEXT = """🤖 *Команды:*

/origin XXX — город вылета (IATA)
/destination XXX — город назначения (IATA, для туда-обратно)
/destination off — отключить туда-обратно (только в одну сторону)
/days N — окно поиска (дней вперёд, 1–90)
/price N — лимит цены за сегмент (€)
/duration N — макс. длительность для базовой цены (мин)
/increment N — доп. € за каждые 30 мин
/direct — вкл/выкл только прямые
/settings — текущие настройки
/reset — сбросить историю отправленных
/help — справка"""

ADMIN_HELP = """
👑 *Админ-команды:*
/approve ID — одобрить пользователя
/reject ID — отклонить
/revoke ID — удалить пользователя (бан)
/users — список пользователей
/userlist — все ID пользователей"""
```

---

## Updated DEFAULT_USER_SETTINGS

```python
DEFAULT_USER_SETTINGS = {
    "origin": "BUD",
    "destination": "",       # NEW: empty = one-way, IATA = round-trip
    "days_ahead": 3,
    "base_price_eur": 20,
    "base_duration_minutes": 90,
    "price_increment_eur": 10,
    "increment_minutes": 30,
    "currency": "eur",
    "market": "hu",
    "limit": 100,
    "direct_only": False,
}
```

---

## Updated COMMAND_MAP

```python
COMMAND_MAP = {
    "/origin": ("origin", str),
    "/destination": ("destination", str),  # NEW
    "/days": ("days_ahead", int),
    "/price": ("base_price_eur", int),
    "/duration": ("base_duration_minutes", int),
    "/increment": ("price_increment_eur", int),
}
```

Handle `/destination` specially: if arg is `"off"` or `""`, set to empty string. Otherwise uppercase the IATA code. Add validation that it's exactly 3 letters.

---

## Migration Path

### What to Remove

1. All GitHub Gist code (`load_state` gist branch, `save_state` gist branch, `GH_TOKEN` env var)
2. `migrate_state()` function (no more legacy format to support)
3. `.github/workflows/check-flights.yml` (no more GitHub Actions)
4. The `getUpdates` polling call in `process_telegram_commands`

### What to Keep

1. `state.json` local file persistence
2. `deal_hash()`, `max_price_for_duration()`, `user_settings()` helper functions
3. `filter_deals()` and `format_deal()` (modify for round-trip support)
4. `fetch_flights()` (make async, keep same API params)
5. All user settings logic, approval flow structure

### What to Add

1. `aiohttp` web server for webhook
2. `setWebhook` call on startup
3. `asyncio` background task for hourly flight check
4. Round-trip search logic
5. `/revoke`, `/userlist`, `/destination` commands
6. Referral question onboarding flow
7. `state["revoked"]` tracking

---

## VPS Deployment

Since the VPS already hosts 2 other bots:

1. Pick an unused port (e.g., 8443, 8444, or whatever is free). Set it in config as `webhook_port`.
2. The VPS likely already has nginx as a reverse proxy with HTTPS. Add a location block for this bot, or use a separate port in the `setWebhook` URL (Telegram supports ports 443, 80, 8443, 88).
3. Run with: `python3 main.py` (persistent process). Use `systemd` service or `tmux`/`screen`.
4. Example systemd unit:

```ini
[Unit]
Description=Flight Deals Bot
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/flight-deals/main.py
WorkingDirectory=/path/to/flight-deals
Restart=always
RestartSec=5
EnvironmentFile=/path/to/flight-deals/.env

[Install]
WantedBy=multi-user.target
```

---

## File Structure After Refactoring

```
flight-deals/
├── main.py              # Everything (webhook server + flight checker + bot logic)
├── config.json          # Secrets (gitignored)
├── state.json           # Persistent state (gitignored)
├── requirements.txt     # aiohttp>=3.9
├── .env                 # Env vars for systemd (gitignored)
├── .gitignore
└── README.md
```

Delete `.github/` directory entirely (no more GitHub Actions).

---

## Summary of Changes at a Glance

| Area | Before | After |
|---|---|---|
| Telegram input | Polling (`getUpdates`) once/hour | Webhook (instant) |
| Scheduling | GitHub Actions cron | `asyncio.sleep(3600)` loop |
| HTTP library | `requests` (sync) | `aiohttp` (async) |
| State storage | Local file + GitHub Gist | Local file only |
| Flight mode | One-way only | One-way + round-trip |
| Onboarding | `/start` → immediate pending | `/start` → referral question → pending |
| Admin tools | `/approve`, `/reject`, `/users` | + `/revoke`, `/userlist` |
| User settings | 8 settings | + `destination` |
| Runtime | Stateless (run & exit) | Persistent process (daemon) |
