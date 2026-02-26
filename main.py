"""
Flight Deal Finder — multi-user Telegram bot.

Features:
  - Multi-user with admin approval (/approve, /reject, /users)
  - Per-user settings and deal deduplication
  - Persistent state via GitHub Gist (CI) or local file
  - Telegram commands to change search parameters
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
# Config & defaults
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state.json"

DEFAULT_USER_SETTINGS = {
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
}


def load_config() -> dict:
    cfg = {
        "aviasales_token": "",
        "telegram_bot_token": "",
        "admin_chat_id": "",
        "gist_id": "",
    }

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))

    # Legacy: treat telegram_chat_id as admin_chat_id
    if not cfg.get("admin_chat_id") and cfg.get("telegram_chat_id"):
        cfg["admin_chat_id"] = str(cfg["telegram_chat_id"])

    env_map = {
        "AVIASALES_TOKEN": "aviasales_token",
        "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
        "TELEGRAM_CHAT_ID": "admin_chat_id",
        "GIST_ID": "gist_id",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            cfg[cfg_key] = val

    cfg["admin_chat_id"] = str(cfg.get("admin_chat_id", ""))
    return cfg


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
# State shape:
# {
#   "users": { "<chat_id>": { "name": "...", "settings": {...}, "sent_deals": [...] } },
#   "pending": { "<chat_id>": { "name": "...", "username": "..." } },
#   "last_update_id": 0
# }


def empty_state() -> dict:
    return {"users": {}, "pending": {}, "last_update_id": 0}


def load_state(cfg: dict) -> dict:
    gist_id = cfg.get("gist_id", "")

    if gist_id:
        try:
            resp = requests.get(f"https://api.github.com/gists/{gist_id}", timeout=10)
            resp.raise_for_status()
            content = resp.json()["files"]["state.json"]["content"]
            state = json.loads(content)
            print(f"📂 State from Gist ({len(state.get('users', {}))} user(s)).")
            return migrate_state(state, cfg)
        except Exception as e:
            print(f"⚠️  Gist load error: {e}")

    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
            print(f"📂 State from file ({len(state.get('users', {}))} user(s)).")
            return migrate_state(state, cfg)
        except Exception:
            pass

    return migrate_state(empty_state(), cfg)


def migrate_state(state: dict, cfg: dict) -> dict:
    """Migrate old single-user state to multi-user format."""
    if "users" in state and "pending" in state:
        # Already new format — make sure admin is in users
        admin_id = cfg.get("admin_chat_id", "")
        if admin_id and admin_id not in state["users"]:
            old_settings = state.pop("settings", {})
            old_sent = state.pop("sent_deals", [])
            state["users"][admin_id] = {
                "name": "Admin",
                "settings": old_settings,
                "sent_deals": old_sent,
            }
        return state

    # Old format: { sent_deals, settings, last_update_id }
    admin_id = cfg.get("admin_chat_id", "")
    new_state = empty_state()
    new_state["last_update_id"] = state.get("last_update_id", 0)
    if admin_id:
        new_state["users"][admin_id] = {
            "name": "Admin",
            "settings": state.get("settings", {}),
            "sent_deals": state.get("sent_deals", []),
        }
    return new_state


def save_state(state: dict, cfg: dict) -> None:
    content = json.dumps(state, ensure_ascii=False, indent=2)

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write(content)

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
            print(f"⚠️  Gist save error: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def deal_hash(deal: dict) -> str:
    key = f"{deal['origin']}-{deal['destination']}-{deal['departure_at']}-{deal['price']}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def max_price_for_duration(duration_minutes: int, s: dict) -> float:
    base_price = s.get("base_price_eur", 20)
    base_dur = s.get("base_duration_minutes", 90)
    increment = s.get("price_increment_eur", 10)
    step = s.get("increment_minutes", 30)

    if duration_minutes <= base_dur:
        return base_price
    extra_steps = math.ceil((duration_minutes - base_dur) / step)
    return base_price + extra_steps * increment


def user_settings(state: dict, chat_id: str) -> dict:
    """Return merged default + user settings."""
    s = dict(DEFAULT_USER_SETTINGS)
    user = state["users"].get(chat_id, {})
    s.update(user.get("settings", {}))
    return s


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

TG_API = "https://api.telegram.org/bot{token}"


FOOTER = "\n\n_by aboutmisha.com_"


def send_tg(text: str, chat_id, cfg: dict, parse_mode: str = None) -> None:
    bot_token = cfg.get("telegram_bot_token", "")
    if not bot_token or not chat_id:
        return
    text += FOOTER
    payload = {"chat_id": str(chat_id), "text": text, "disable_web_page_preview": True, "parse_mode": "Markdown"}
    try:
        requests.post(f"{TG_API.format(token=bot_token)}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        print(f"⚠️  TG send error: {e}")


HELP_TEXT = """🤖 *Команды:*

/origin XXX — город вылета (IATA)
/days N — дней вперёд
/price N — базовая цена (€)
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
/users — список пользователей"""

COMMAND_MAP = {
    "/origin": ("origin", str),
    "/days": ("days_ahead", int),
    "/price": ("base_price_eur", int),
    "/duration": ("base_duration_minutes", int),
    "/increment": ("price_increment_eur", int),
}


def process_telegram_commands(cfg: dict, state: dict) -> None:
    bot_token = cfg.get("telegram_bot_token", "")
    admin_id = cfg.get("admin_chat_id", "")
    if not bot_token:
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
        print(f"⚠️  TG getUpdates error: {e}")
        return

    for upd in updates:
        state["last_update_id"] = upd["update_id"]
        msg = upd.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not chat_id:
            continue

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        from_user = msg.get("from", {})
        display_name = from_user.get("first_name", "?")
        username = from_user.get("username", "")

        is_admin = chat_id == admin_id
        is_approved = chat_id in state["users"]

        # --- /start: registration flow ---
        if cmd == "/start":
            if is_approved:
                help_msg = HELP_TEXT
                if is_admin:
                    help_msg += ADMIN_HELP
                send_tg(help_msg, chat_id, cfg, parse_mode="Markdown")
            elif chat_id in state.get("pending", {}):
                send_tg("⏳ Ваш запрос уже отправлен. Ожидайте одобрения.", chat_id, cfg)
            else:
                state.setdefault("pending", {})[chat_id] = {
                    "name": display_name,
                    "username": username,
                }
                send_tg("📨 Запрос отправлен! Ожидайте одобрения администратором.", chat_id, cfg)
                uname = f" (@{username})" if username else ""
                send_tg(
                    f"🆕 Новый запрос от *{display_name}*{uname}\nID: `{chat_id}`\n\n"
                    f"Одобрить: `/approve {chat_id}`\nОтклонить: `/reject {chat_id}`",
                    admin_id, cfg, parse_mode="Markdown",
                )
            continue

        # --- Commands only for approved users ---
        if not is_approved:
            send_tg("⛔ Вы не авторизованы. Отправьте /start для запроса доступа.", chat_id, cfg)
            continue

        # --- /help ---
        if cmd == "/help":
            help_msg = HELP_TEXT
            if is_admin:
                help_msg += ADMIN_HELP
            send_tg(help_msg, chat_id, cfg, parse_mode="Markdown")

        # --- /settings ---
        elif cmd == "/settings":
            s = user_settings(state, chat_id)
            lines = [
                f"🏙 Откуда: `{s['origin']}`",
                f"📅 Дней вперёд: `{s['days_ahead']}`",
                f"💰 Базовая цена: `{s['base_price_eur']}€`",
                f"⏱ Длительность: `{s['base_duration_minutes']} мин`",
                f"📈 Шаг: `+{s['price_increment_eur']}€ / {s['increment_minutes']} мин`",
                f"✈️ Только прямые: `{'да' if s.get('direct_only') else 'нет'}`",
                f"📊 Отправлено: `{len(state['users'][chat_id].get('sent_deals', []))}`",
            ]
            send_tg("\n".join(lines), chat_id, cfg, parse_mode="Markdown")

        # --- /direct toggle ---
        elif cmd == "/direct":
            settings = state["users"][chat_id].setdefault("settings", {})
            current = settings.get("direct_only", DEFAULT_USER_SETTINGS["direct_only"])
            settings["direct_only"] = not current
            status = "✅ Только прямые" if settings["direct_only"] else "❌ Все рейсы"
            send_tg(status, chat_id, cfg)

        # --- /reset ---
        elif cmd == "/reset":
            state["users"][chat_id]["sent_deals"] = []
            send_tg("🗑 История очищена.", chat_id, cfg)

        # --- setting commands ---
        elif cmd in COMMAND_MAP:
            key, typ = COMMAND_MAP[cmd]
            if not arg:
                send_tg(f"⚠️ `{cmd} <значение>`", chat_id, cfg, parse_mode="Markdown")
            else:
                try:
                    val = typ(arg.upper()) if typ == str else typ(arg)
                    state["users"][chat_id].setdefault("settings", {})[key] = val
                    send_tg(f"✅ `{key}` = `{val}`", chat_id, cfg, parse_mode="Markdown")
                except ValueError:
                    send_tg(f"⚠️ Неверное значение: {arg}", chat_id, cfg)

        # --- Admin commands ---
        elif cmd == "/approve" and is_admin:
            if not arg:
                send_tg("⚠️ `/approve <chat_id>`", chat_id, cfg, parse_mode="Markdown")
            elif arg in state.get("pending", {}):
                info = state["pending"].pop(arg)
                state["users"][arg] = {
                    "name": info.get("name", "?"),
                    "settings": {},
                    "sent_deals": [],
                }
                send_tg(f"✅ Пользователь {info.get('name', arg)} одобрен.", chat_id, cfg)
                send_tg("🎉 Вы одобрены! Отправьте /help для списка команд.", arg, cfg)
            else:
                send_tg(f"❓ ID `{arg}` не найден в ожидающих.", chat_id, cfg, parse_mode="Markdown")

        elif cmd == "/reject" and is_admin:
            if not arg:
                send_tg("⚠️ `/reject <chat_id>`", chat_id, cfg, parse_mode="Markdown")
            elif arg in state.get("pending", {}):
                info = state["pending"].pop(arg)
                send_tg(f"❌ Запрос от {info.get('name', arg)} отклонён.", chat_id, cfg)
                send_tg("❌ Ваш запрос отклонён администратором.", arg, cfg)
            else:
                send_tg(f"❓ ID `{arg}` не найден в ожидающих.", chat_id, cfg, parse_mode="Markdown")

        elif cmd == "/users" and is_admin:
            lines = ["👥 *Пользователи:*"]
            for uid, u in state["users"].items():
                s = dict(DEFAULT_USER_SETTINGS)
                s.update(u.get("settings", {}))
                admin_tag = " 👑" if uid == admin_id else ""
                lines.append(f"• {u.get('name', '?')}{admin_tag} — `{s['origin']}`, {s['days_ahead']}д, {s['base_price_eur']}€")
            pending = state.get("pending", {})
            if pending:
                lines.append(f"\n⏳ *Ожидают ({len(pending)}):*")
                for pid, p in pending.items():
                    lines.append(f"• {p.get('name', '?')} — `/approve {pid}`")
            send_tg("\n".join(lines), chat_id, cfg, parse_mode="Markdown")

        else:
            send_tg("❓ Неизвестная команда. /help", chat_id, cfg)

    if updates:
        print(f"📨 Processed {len(updates)} message(s).")


# ---------------------------------------------------------------------------
# Aviasales API
# ---------------------------------------------------------------------------

API_BASE = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def fetch_flights(departure_date: str, origin: str, cfg: dict, s: dict) -> list[dict]:
    params = {
        "origin": origin,
        "departure_at": departure_date,
        "one_way": "true",
        "currency": s.get("currency", "eur"),
        "market": s.get("market", "hu"),
        "limit": s.get("limit", 100),
        "sorting": "price",
        "token": cfg["aviasales_token"],
    }
    if s.get("direct_only"):
        params["direct"] = "true"

    resp = requests.get(API_BASE, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return []
    return data.get("data", [])


def filter_deals(tickets: list[dict], s: dict) -> list[dict]:
    deals = []
    for t in tickets:
        duration = t.get("duration_to") or t.get("duration", 0)
        if duration <= 0:
            continue
        price = t.get("price", 0)
        threshold = max_price_for_duration(duration, s)
        if price <= threshold:
            deals.append({
                "origin": t.get("origin", s.get("origin", "?")),
                "destination": t.get("destination", "???"),
                "departure_at": t.get("departure_at", ""),
                "price": price,
                "currency": s.get("currency", "eur").upper(),
                "duration_min": duration,
                "threshold": threshold,
                "airline": t.get("airline", ""),
                "flight_number": t.get("flight_number", ""),
                "transfers": t.get("transfers", 0),
                "link": t.get("link", ""),
            })
    return deals


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


# ---------------------------------------------------------------------------
# Search per user
# ---------------------------------------------------------------------------


def search_for_user(chat_id: str, state: dict, cfg: dict) -> None:
    s = user_settings(state, chat_id)
    user_data = state["users"][chat_id]
    sent_hashes = set(user_data.get("sent_deals", []))

    days_ahead = s.get("days_ahead", 3)
    today = datetime.utcnow().date()
    all_new: list[dict] = []

    for delta in range(days_ahead):
        day = today + timedelta(days=delta)
        tickets = fetch_flights(day.isoformat(), s["origin"], cfg, s)
        deals = filter_deals(tickets, s)

        for d in deals:
            h = deal_hash(d)
            if h not in sent_hashes:
                d["_hash"] = h
                all_new.append(d)

    name = user_data.get("name", chat_id)
    print(f"  👤 {name}: {len(all_new)} new deal(s)")

    if all_new:
        all_new.sort(key=lambda d: d["price"])

        header = f"🔥 {len(all_new)} new cheap flight(s)!\n\n"
        body = "\n\n".join(format_deal(d) for d in all_new)
        text = header + body
        if len(text) > 4096:
            text = text[:4090] + "\n…"

        send_tg(text, chat_id, cfg)

        sent_list = user_data.get("sent_deals", [])
        for d in all_new:
            sent_list.append(d["_hash"])
        user_data["sent_deals"] = sent_list[-500:]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    cfg = load_config()

    if not cfg.get("aviasales_token"):
        print("❌ aviasales_token not set.")
        sys.exit(1)

    state = load_state(cfg)

    # Ensure admin is in users
    admin_id = cfg.get("admin_chat_id", "")
    if admin_id and admin_id not in state["users"]:
        state["users"][admin_id] = {"name": "Admin", "settings": {}, "sent_deals": []}

    # Process Telegram commands
    process_telegram_commands(cfg, state)

    # Search for each user
    print(f"\n🔍 Searching for {len(state['users'])} user(s)…")
    for chat_id in state["users"]:
        try:
            search_for_user(chat_id, state, cfg)
        except Exception as e:
            print(f"  ❌ Error for {chat_id}: {e}")

    save_state(state, cfg)
    print("✅ Done.")


if __name__ == "__main__":
    main()
