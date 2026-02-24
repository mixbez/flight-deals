"""
Flight Deal Finder — searches cheap one-way flights via Aviasales (Travelpayouts) API
and sends Telegram notifications when deals are found.

Features:
  - Deduplication: already-sent deals are not sent again
  - Telegram commands: change settings by messaging the bot
  - Persistent state via GitHub Gist (CI) or local file
"""

import hashlib
import json
import math
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state.json"

DEFAULTS = {
    "origin": "BUD",
    "days_ahead": 3,
    "base_price_eur": 20,
    "base_duration_minutes": 90,
    "price_increment_eur": 10,
    "increment_minutes": 30,
    "currency": "eur",
    "market": "hu",
    "limit": 100,
    "direct_only": False,
    "aviasales_token": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "gist_id": "",
}


def load_config() -> dict:
    """Load configuration from JSON file (if exists), with env-var overrides."""
    cfg = dict(DEFAULTS)

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))

    env_map = {
        "AVIASALES_TOKEN": "aviasales_token",
        "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
        "TELEGRAM_CHAT_ID": "telegram_chat_id",
        "GIST_ID": "gist_id",
        "FLIGHT_ORIGIN": "origin",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val

    if os.environ.get("FLIGHT_DAYS_AHEAD"):
        cfg["days_ahead"] = int(os.environ["FLIGHT_DAYS_AHEAD"])

    return cfg


# ---------------------------------------------------------------------------
# State persistence (local file + GitHub Gist)
# ---------------------------------------------------------------------------


def load_state(cfg: dict) -> dict:
    """Load state from Gist (if configured) or local file."""
    default_state = {"sent_deals": [], "settings": {}, "last_update_id": 0}

    gist_id = cfg.get("gist_id", "")

    # Try Gist first
    if gist_id:
        try:
            resp = requests.get(f"https://api.github.com/gists/{gist_id}", timeout=10)
            resp.raise_for_status()
            gist = resp.json()
            content = gist["files"]["state.json"]["content"]
            state = json.loads(content)
            print(f"📂 State loaded from Gist ({len(state.get('sent_deals', []))} sent deals).")
            return state
        except Exception as e:
            print(f"⚠️  Could not load Gist state: {e}")

    # Fall back to local file
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
            print(f"📂 State loaded from local file ({len(state.get('sent_deals', []))} sent deals).")
            return state
        except Exception:
            pass

    return default_state


def save_state(state: dict, cfg: dict) -> None:
    """Save state to Gist (if configured) and local file."""
    content = json.dumps(state, ensure_ascii=False, indent=2)

    # Save locally
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    # Save to Gist
    gist_id = cfg.get("gist_id", "")
    gh_token = os.environ.get("GH_TOKEN", "")
    if gist_id and gh_token:
        try:
            resp = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers={"Authorization": f"token {gh_token}"},
                json={"files": {"state.json": {"content": content}}},
                timeout=10,
            )
            resp.raise_for_status()
            print("💾 State saved to Gist.")
        except Exception as e:
            print(f"⚠️  Could not save Gist state: {e}")
    elif gist_id:
        print("⚠️  GIST_ID set but GH_TOKEN missing — Gist not updated.")


# ---------------------------------------------------------------------------
# Deal deduplication
# ---------------------------------------------------------------------------


def deal_hash(deal: dict) -> str:
    """Generate a unique hash for a deal to detect duplicates."""
    key = f"{deal['origin']}-{deal['destination']}-{deal['departure_at']}-{deal['price']}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def filter_new_deals(deals: list[dict], sent: list[str]) -> list[dict]:
    """Remove deals that were already sent."""
    sent_set = set(sent)
    new = []
    for d in deals:
        h = deal_hash(d)
        if h not in sent_set:
            d["_hash"] = h
            new.append(d)
    return new


# ---------------------------------------------------------------------------
# Telegram bot commands
# ---------------------------------------------------------------------------

TG_API = "https://api.telegram.org/bot{token}"

HELP_TEXT = """🤖 *Flight Deal Finder — команды:*

/origin XXX — город вылета (IATA, напр. BUD)
/days N — сколько дней вперёд искать
/price N — базовая цена (€) для коротких рейсов
/duration N — макс. длительность для базовой цены (мин)
/increment N — доп. € за каждые 30 мин
/direct — вкл/выкл только прямые рейсы
/settings — текущие настройки
/reset — очистить историю отправленных
/help — эта справка"""

COMMAND_MAP = {
    "/origin": ("origin", str),
    "/days": ("days_ahead", int),
    "/price": ("base_price_eur", int),
    "/duration": ("base_duration_minutes", int),
    "/increment": ("price_increment_eur", int),
}


def send_tg(text: str, cfg: dict, parse_mode: str = None) -> None:
    """Send a message via Telegram bot."""
    bot_token = cfg.get("telegram_bot_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    if not bot_token or not chat_id:
        return

    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        requests.post(
            f"{TG_API.format(token=bot_token)}/sendMessage",
            json=payload,
            timeout=15,
        )
    except Exception as e:
        print(f"⚠️  Telegram send error: {e}")


def process_telegram_commands(cfg: dict, state: dict) -> None:
    """Read new Telegram messages and process bot commands."""
    bot_token = cfg.get("telegram_bot_token", "")
    chat_id = str(cfg.get("telegram_chat_id", ""))
    if not bot_token or not chat_id:
        return

    last_id = state.get("last_update_id", 0)
    try:
        resp = requests.get(
            f"{TG_API.format(token=bot_token)}/getUpdates",
            params={"offset": last_id + 1, "timeout": 0},
            timeout=10,
        )
        updates = resp.json().get("result", [])
    except Exception as e:
        print(f"⚠️  Telegram getUpdates error: {e}")
        return

    settings = state.setdefault("settings", {})

    for upd in updates:
        state["last_update_id"] = upd["update_id"]
        msg = upd.get("message", {})

        if str(msg.get("chat", {}).get("id")) != chat_id:
            continue

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/help", "/start"):
            send_tg(HELP_TEXT, cfg, parse_mode="Markdown")

        elif cmd == "/settings":
            merged = dict(DEFAULTS)
            merged.update(settings)
            lines = [
                f"🏙 Откуда: `{merged['origin']}`",
                f"📅 Дней вперёд: `{merged['days_ahead']}`",
                f"💰 Базовая цена: `{merged['base_price_eur']}€`",
                f"⏱ Базовая длительность: `{merged['base_duration_minutes']} мин`",
                f"📈 Шаг цены: `+{merged['price_increment_eur']}€ / {merged.get('increment_minutes', 30)} мин`",
                f"✈️ Только прямые: `{'да' if merged.get('direct_only') else 'нет'}`",
                f"📊 Отправлено рейсов: `{len(state.get('sent_deals', []))}`",
            ]
            send_tg("\n".join(lines), cfg, parse_mode="Markdown")

        elif cmd == "/direct":
            current = settings.get("direct_only", DEFAULTS["direct_only"])
            settings["direct_only"] = not current
            status = "✅ Только прямые рейсы" if settings["direct_only"] else "❌ Все рейсы (с пересадками)"
            send_tg(status, cfg)

        elif cmd == "/reset":
            state["sent_deals"] = []
            send_tg("🗑 История отправленных рейсов очищена.", cfg)

        elif cmd in COMMAND_MAP:
            key, typ = COMMAND_MAP[cmd]
            if not arg:
                send_tg(f"⚠️ Укажите значение: `{cmd} <значение>`", cfg, parse_mode="Markdown")
            else:
                try:
                    val = typ(arg.upper()) if typ == str else typ(arg)
                    settings[key] = val
                    send_tg(f"✅ `{key}` = `{val}`", cfg, parse_mode="Markdown")
                except ValueError:
                    send_tg(f"⚠️ Неверное значение: {arg}", cfg)

        else:
            send_tg("❓ Неизвестная команда. /help для списка.", cfg)

    if updates:
        print(f"📨 Processed {len(updates)} Telegram message(s).")


# ---------------------------------------------------------------------------
# Price threshold logic
# ---------------------------------------------------------------------------


def max_price_for_duration(duration_minutes: int, cfg: dict) -> float:
    base_price = cfg.get("base_price_eur", 20)
    base_dur = cfg.get("base_duration_minutes", 90)
    increment = cfg.get("price_increment_eur", 10)
    step = cfg.get("increment_minutes", 30)

    if duration_minutes <= base_dur:
        return base_price

    extra = duration_minutes - base_dur
    extra_steps = math.ceil(extra / step)
    return base_price + extra_steps * increment


# ---------------------------------------------------------------------------
# Aviasales API
# ---------------------------------------------------------------------------

API_BASE = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def fetch_flights(departure_date: str, cfg: dict) -> list[dict]:
    params = {
        "origin": cfg["origin"],
        "departure_at": departure_date,
        "one_way": "true",
        "currency": cfg.get("currency", "eur"),
        "market": cfg.get("market", "hu"),
        "limit": cfg.get("limit", 100),
        "sorting": "price",
        "token": cfg["aviasales_token"],
    }
    if cfg.get("direct_only"):
        params["direct"] = "true"

    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success"):
        print(f"  API error for {departure_date}: {data.get('error')}")
        return []

    return data.get("data", [])


# ---------------------------------------------------------------------------
# Filter deals
# ---------------------------------------------------------------------------


def filter_deals(tickets: list[dict], cfg: dict) -> list[dict]:
    deals = []
    for t in tickets:
        duration = t.get("duration_to") or t.get("duration", 0)
        if duration <= 0:
            continue

        price = t.get("price", 0)
        threshold = max_price_for_duration(duration, cfg)

        if price <= threshold:
            deals.append({
                "origin": t.get("origin", cfg["origin"]),
                "destination": t.get("destination", "???"),
                "departure_at": t.get("departure_at", ""),
                "price": price,
                "currency": cfg.get("currency", "eur").upper(),
                "duration_min": duration,
                "threshold": threshold,
                "airline": t.get("airline", ""),
                "flight_number": t.get("flight_number", ""),
                "transfers": t.get("transfers", 0),
                "link": t.get("link", ""),
            })
    return deals


# ---------------------------------------------------------------------------
# Formatting & notification
# ---------------------------------------------------------------------------


def format_deal(d: dict) -> str:
    dur_h = d["duration_min"] // 60
    dur_m = d["duration_min"] % 60
    dep = d["departure_at"][:16].replace("T", " ") if d["departure_at"] else "?"
    stops = "direct" if d["transfers"] == 0 else f"{d['transfers']} stop(s)"

    link = ""
    if d.get("link"):
        link = f"\nhttps://www.aviasales.com{d['link']}"

    return (
        f"✈️ {d['origin']} → {d['destination']}\n"
        f"   {dep} | {dur_h}h{dur_m:02d}m | {stops}\n"
        f"   💰 {d['price']} {d['currency']} (limit {d['threshold']:.0f} {d['currency']})\n"
        f"   {d['airline']} {d['flight_number']}{link}"
    )


def send_deals_telegram(deals: list[dict], cfg: dict) -> None:
    if not deals:
        return

    header = f"🔥 Found {len(deals)} new cheap flight(s)!\n\n"
    body = "\n\n".join(format_deal(d) for d in deals)
    text = header + body

    if len(text) > 4096:
        text = text[:4090] + "\n…"

    send_tg(text, cfg)
    print(f"✅ Telegram message sent ({len(deals)} deal(s)).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    cfg = load_config()

    if not cfg.get("aviasales_token"):
        print("❌ aviasales_token is not set.")
        sys.exit(1)

    # Load persistent state
    state = load_state(cfg)

    # Process Telegram commands & apply settings overrides
    process_telegram_commands(cfg, state)
    cfg.update(state.get("settings", {}))

    # Search flights
    days_ahead = cfg.get("days_ahead", 3)
    today = datetime.utcnow().date()
    all_deals: list[dict] = []

    for delta in range(days_ahead):
        day = today + timedelta(days=delta)
        date_str = day.isoformat()
        print(f"🔍 Searching flights on {date_str} from {cfg['origin']}…")

        tickets = fetch_flights(date_str, cfg)
        print(f"   Got {len(tickets)} ticket(s) from cache.")

        deals = filter_deals(tickets, cfg)
        if deals:
            print(f"   🎉 {len(deals)} deal(s) match price threshold.")
        all_deals.extend(deals)

    # Deduplicate
    sent_hashes = state.get("sent_deals", [])
    new_deals = filter_new_deals(all_deals, sent_hashes)

    print(f"\n{'='*50}")
    print(f"Total matching: {len(all_deals)} | New (not sent before): {len(new_deals)}")

    if new_deals:
        new_deals.sort(key=lambda d: d["price"])

        for d in new_deals:
            print(format_deal(d))
            print()

        send_deals_telegram(new_deals, cfg)

        # Record sent deals (keep last 500 to avoid unbounded growth)
        for d in new_deals:
            sent_hashes.append(d["_hash"])
        state["sent_deals"] = sent_hashes[-500:]
    else:
        print("No new deals found this time.")

    # Save state
    save_state(state, cfg)


if __name__ == "__main__":
    main()
