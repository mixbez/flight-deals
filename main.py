"""
Flight Deal Finder — webhook-based persistent Telegram bot.

Features:
  - Webhook-based Telegram integration (instant command processing)
  - Hourly background flight search (asyncio)
  - Multi-user with admin approval workflow
  - One-way and round-trip flight search
  - Month-level API batching for Aviasales
  - Referral question onboarding
  - Admin commands: /approve, /reject, /revoke, /userlist, /users
"""

print("[STARTUP] Script loaded, imports starting...")

import asyncio
import hashlib
import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout

# Write startup marker to file
with open('/app/startup.txt', 'w') as f:
    f.write('Script started at module import time\n')

# Setup logging with unbuffered output AND file logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,
    force=True
)
# Also add file handler for persistent logs
file_handler = logging.FileHandler('/app/bot.log')
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
file_handler.setFormatter(formatter)
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger(__name__)
logger.info("Logging initialized")

# ---------------------------------------------------------------------------
# Constants & Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path("/app/data") / "state.json"

TG_API = "https://api.telegram.org/bot{token}"
AVIASALES_API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

FOOTER = "\n\n_🤖 Flight Deals Bot_"

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

DEFAULT_USER_SETTINGS = {
    "origin": "BUD",
    "destination": "",  # empty = one-way, IATA code = round-trip
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

COMMAND_MAP = {
    "/origin": ("origin", str),
    "/destination": ("destination", str),
    "/days": ("days_ahead", int),
    "/price": ("base_price_eur", int),
    "/duration": ("base_duration_minutes", int),
    "/increment": ("price_increment_eur", int),
}

# Global session for async HTTP calls
session: ClientSession = None


# ---------------------------------------------------------------------------
# Config & State Management
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load config from file and environment variables."""
    logger.debug("🔧 load_config() called")

    cfg = {
        "aviasales_token": "",
        "telegram_bot_token": "",
        "admin_chat_id": "",
        "webhook_host": "",
        "webhook_port": 443,  # Port for Telegram (in webhook URL)
        "listen_port": 8080,  # Port for aiohttp server to listen on (CHANGED FROM 8443!)
        "webhook_path": "/webhook-flightdeals",
    }
    logger.debug(f"📝 Default config: webhook_port={cfg['webhook_port']}, listen_port={cfg['listen_port']}")

    if CONFIG_PATH.exists():
        logger.debug(f"📂 Loading config from {CONFIG_PATH}")
        with open(CONFIG_PATH, encoding="utf-8") as f:
            file_cfg = json.load(f)
            logger.debug(f"📄 File config: {json.dumps(file_cfg)}")
            cfg.update(file_cfg)
        logger.debug(f"✅ After file update: webhook_port={cfg['webhook_port']}, listen_port={cfg['listen_port']}")

    # Environment overrides
    env_map = {
        "AVIASALES_TOKEN": "aviasales_token",
        "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
        "TELEGRAM_CHAT_ID": "admin_chat_id",
        "WEBHOOK_HOST": "webhook_host",
        "WEBHOOK_PORT": "webhook_port",
        "LISTEN_PORT": "listen_port",
        "WEBHOOK_PATH": "webhook_path",
    }
    logger.debug("🌍 Checking environment variables...")
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            if cfg_key in ["webhook_port", "listen_port"]:
                cfg[cfg_key] = int(val)
                logger.debug(f"  {env_key}={val} → cfg['{cfg_key}']={cfg[cfg_key]} (int)")
            else:
                cfg[cfg_key] = val
                logger.debug(f"  {env_key}={val} → cfg['{cfg_key}']")
        else:
            logger.debug(f"  {env_key} not set")

    cfg["admin_chat_id"] = str(cfg.get("admin_chat_id", ""))
    cfg["webhook_port"] = int(cfg.get("webhook_port", 443))
    cfg["listen_port"] = int(cfg.get("listen_port", 8080))
    logger.debug(f"✅ Final config: webhook_port={cfg['webhook_port']}, listen_port={cfg['listen_port']}")
    return cfg


def empty_state() -> dict:
    """Return empty state structure."""
    return {
        "users": {},
        "pending": {},
        "revoked": {},
        "last_update_id": 0,
    }


def load_state(cfg: dict) -> dict:
    """Load state from local file."""
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
            print(f"📂 State loaded: {len(state.get('users', {}))} user(s)")
            return state
    else:
        return empty_state()


def save_state(state: dict, cfg: dict) -> None:
    """Save state to local file."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def user_settings(state: dict, chat_id: str) -> dict:
    """Get user settings with defaults."""
    user = state["users"].get(str(chat_id), {})
    settings = user.get("settings", {})
    return {**DEFAULT_USER_SETTINGS, **settings}


def deal_hash(deal: dict) -> str:
    """Generate hash for deduplication."""
    try:
        # Check if this is a round-trip deal (has outbound and return info)
        if "_outbound" in deal and "_return" in deal:
            h = hashlib.md5(
                f"{deal['_outbound']['origin']}-{deal['_outbound']['destination']}-"
                f"{deal['_outbound']['departure_at']}-{deal['_return']['departure_at']}-"
                f"{deal['_total_price']}"
                .encode()
            ).hexdigest()
        else:
            # One-way deal
            dep_date = deal.get("departure_at", "").split("T")[0]
            h = hashlib.md5(
                f"{deal.get('origin', '')}-{deal.get('destination', '')}-"
                f"{dep_date}-{deal.get('price', '')}"
                .encode()
            ).hexdigest()
        return h
    except Exception:
        return hashlib.md5(str(deal).encode()).hexdigest()


def max_price_for_duration(duration_minutes: int, settings: dict) -> int:
    """Calculate max price based on flight duration."""
    base = settings["base_price_eur"]
    base_duration = settings["base_duration_minutes"]

    if duration_minutes <= base_duration:
        return base

    extra_minutes = duration_minutes - base_duration
    extra_30_min_blocks = math.ceil(extra_minutes / settings["increment_minutes"])
    extra_cost = extra_30_min_blocks * settings["price_increment_eur"]
    return base + extra_cost


def filter_deals(tickets: list, settings: dict) -> list:
    """Filter deals based on user settings."""
    result = []
    for ticket in tickets:
        # Extract price and duration
        price = ticket.get("price")
        duration = ticket.get("duration", 0)

        if price is None:
            continue

        # Check price vs max price for this duration
        max_p = max_price_for_duration(duration, settings)
        if price > max_p:
            continue

        # Check direct-only if enabled
        if settings.get("direct_only") and ticket.get("transfers", 1) != 0:
            continue

        # Passed all filters
        result.append(ticket)

    return result


def format_deal(deal: dict, is_round_trip: bool = False) -> str:
    """Format a deal for display."""
    if is_round_trip:
        # Round-trip format
        outbound = deal["_outbound"]
        ret = deal["_return"]
        total = deal["_total_price"]

        origin = outbound.get("origin", "?")
        dest = outbound.get("destination", "?")

        msg = f"✈️ {origin} ↔ {dest} — {total} EUR total\n\n"

        # Outbound leg
        out_date = outbound.get("departure_at", "").split("T")[0]
        out_time = outbound.get("departure_at", "").split("T")[1][:5] if "T" in outbound.get("departure_at", "") else ""
        out_duration = outbound.get("duration", 0)
        out_hours = out_duration // 60
        out_mins = out_duration % 60
        out_duration_str = f"{out_hours}h{out_mins:02d}m"
        out_airline = outbound.get("airline", "")
        out_flight = outbound.get("flight_number", "")
        out_price = outbound.get("price", "?")
        out_direct = "direct" if outbound.get("transfers", 1) == 0 else "1+ stops"
        out_url = outbound.get("search_url", "")

        msg += f"  → {origin} → {dest}\n"
        msg += f"    {out_date} {out_time} | {out_duration_str} | {out_direct}\n"
        msg += f"    💰 {out_price} EUR\n"
        if out_airline and out_flight:
            msg += f"    {out_airline} {out_flight}\n"
        if out_url:
            msg += f"    {out_url}\n"

        msg += "\n"

        # Return leg
        ret_date = ret.get("departure_at", "").split("T")[0]
        ret_time = ret.get("departure_at", "").split("T")[1][:5] if "T" in ret.get("departure_at", "") else ""
        ret_duration = ret.get("duration", 0)
        ret_hours = ret_duration // 60
        ret_mins = ret_duration % 60
        ret_duration_str = f"{ret_hours}h{ret_mins:02d}m"
        ret_airline = ret.get("airline", "")
        ret_flight = ret.get("flight_number", "")
        ret_price = ret.get("price", "?")
        ret_direct = "direct" if ret.get("transfers", 1) == 0 else "1+ stops"
        ret_url = ret.get("search_url", "")

        msg += f"  ← {dest} → {origin}\n"
        msg += f"    {ret_date} {ret_time} | {ret_duration_str} | {ret_direct}\n"
        msg += f"    💰 {ret_price} EUR\n"
        if ret_airline and ret_flight:
            msg += f"    {ret_airline} {ret_flight}\n"
        if ret_url:
            msg += f"    {ret_url}\n"
        msg += f"   by aboutmisha.com\n"

        return msg
    else:
        # One-way format
        origin = deal.get("origin", "?")
        dest = deal.get("destination", "?")
        date_str = deal.get("departure_at", "").split("T")[0]
        time_str = deal.get("departure_at", "").split("T")[1][:5] if "T" in deal.get("departure_at", "") else ""
        price = deal.get("price", "?")
        duration = deal.get("duration", 0)
        hours = duration // 60
        mins = duration % 60
        duration_str = f"{hours}h{mins:02d}m"
        direct = "direct" if deal.get("transfers", 1) == 0 else "1+ stops"
        airline = deal.get("airline", "")
        flight = deal.get("flight_number", "")
        url = deal.get("search_url", "")

        msg = f"✈️ {origin} → {dest} — {price} EUR\n"
        msg += f"   {date_str} {time_str} | {duration_str} | {direct}\n"
        msg += f"   💰 {price} EUR\n"
        if airline and flight:
            msg += f"   {airline} {flight}\n"
        if url:
            msg += f"   {url}\n"
        msg += f"   by aboutmisha.com\n"

        return msg


# ---------------------------------------------------------------------------
# Telegram API Calls
# ---------------------------------------------------------------------------

async def send_tg(text: str, chat_id: str, cfg: dict, parse_mode: str = "Markdown", reply_markup: dict = None) -> bool:
    """Send message via Telegram API.

    reply_markup: Optional inline keyboard dict with structure:
        {"inline_keyboard": [[{"text": "Button", "callback_data": "data"}]]}
    """
    logger.debug(f"📤 send_tg called: chat_id={chat_id}, text_len={len(text)}, parse_mode={parse_mode}")
    text = text + FOOTER
    payload = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": True,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        logger.debug(f"📲 Posting message to Telegram API for chat_id={chat_id}")
        async with session.post(
            f"{TG_API.format(token=cfg['telegram_bot_token'])}/sendMessage",
            json=payload,
            timeout=ClientTimeout(total=15)
        ) as resp:
            logger.debug(f"📊 Telegram API response: status={resp.status}")
            if resp.status == 200:
                logger.debug(f"✅ Message sent successfully to {chat_id}")
                return True
            else:
                logger.error(f"❌ Telegram API returned status {resp.status}")
    except Exception as e:
        logger.error(f"❌ send_tg error: {e}", exc_info=True)
    return False


async def set_webhook(cfg: dict) -> None:
    """Register webhook with Telegram."""
    if not cfg.get("webhook_host"):
        print("⚠️ webhook_host not set, skipping webhook registration")
        return

    # Don't add port if it's 443 (standard HTTPS)
    port = cfg.get("webhook_port", 443)
    if port == 443:
        url = f"{cfg['webhook_host']}{cfg['webhook_path']}"
    else:
        url = f"{cfg['webhook_host']}:{port}{cfg['webhook_path']}"

    payload = {"url": url}

    try:
        async with session.post(
            f"{TG_API.format(token=cfg['telegram_bot_token'])}/setWebhook",
            json=payload,
            timeout=ClientTimeout(total=15)
        ) as resp:
            if resp.status == 200:
                print(f"✅ Webhook set: {url}")
            else:
                print(f"❌ Webhook setup failed: {resp.status}")
    except Exception as e:
        print(f"❌ set_webhook error: {e}")


# ---------------------------------------------------------------------------
# Flight API
# ---------------------------------------------------------------------------

async def fetch_flights(departure_month: str, origin: str, destination: str, cfg: dict, settings: dict) -> list:
    """
    Fetch flights for an entire month.

    departure_month: "2026-03" format
    Returns: list of all tickets for that month
    """
    params = {
        "origin": origin,
        "departure_at": departure_month,
        "one_way": "true",
        "currency": settings.get("currency", "eur"),
        "market": settings.get("market", "hu"),
        "limit": settings.get("limit", 100),
        "sorting": "price",
        "token": cfg["aviasales_token"],
    }

    if destination:
        params["destination"] = destination

    if settings.get("direct_only"):
        params["direct"] = "true"

    logger.debug(f"🔍 fetch_flights: {origin} → {destination if destination else '(любой)'} | {departure_month}")
    logger.debug(f"   Параметры: {params}")

    try:
        async with session.get(
            AVIASALES_API,
            params=params,
            timeout=ClientTimeout(total=30)
        ) as resp:
            logger.debug(f"   API статус: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                logger.debug(f"   API ответ: success={data.get('success')}, flights={len(data.get('data', []))}")
                if data.get("success"):
                    return data.get("data", [])
            else:
                text = await resp.text()
                logger.error(f"   API ошибка: {resp.status} - {text[:200]}")
    except Exception as e:
        logger.error(f"❌ fetch_flights error: {e}")

    return []


async def search_for_user(chat_id: str, state: dict, cfg: dict, notify_if_empty: bool = False) -> None:
    """Search for flights for a single user.

    notify_if_empty: If True, send message when no flights found (used for /require command)
    """
    logger.info(f"🔍 search_for_user called for {chat_id}")
    user_data = state["users"].get(str(chat_id), {})
    settings = user_settings(state, chat_id)
    sent_hashes = set(user_data.get("sent_deals", []))
    logger.debug(f"   Settings: origin={settings['origin']}, destination={settings.get('destination')}")

    origin = settings["origin"]
    destination = settings.get("destination", "").strip().upper()
    days_ahead = max(1, min(settings.get("days_ahead", 3), 90))

    today = datetime.utcnow().date()
    end_date = today + timedelta(days=days_ahead)

    # Determine which months to fetch
    months_to_fetch = set()
    current = today
    while current <= end_date:
        months_to_fetch.add(current.strftime("%Y-%m"))
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)

    all_new = []

    if not destination:
        # One-way mode
        for month_str in sorted(months_to_fetch):
            tickets = await fetch_flights(month_str, origin, "", cfg, settings)

            # Filter to only dates in our range
            for ticket in tickets:
                dep_date_str = ticket.get("departure_at", "")
                if not dep_date_str:
                    continue

                try:
                    dep_date = datetime.fromisoformat(dep_date_str.split("T")[0]).date()
                except Exception:
                    continue

                if dep_date < today or dep_date > end_date:
                    continue

                deals = filter_deals([ticket], settings)
                for d in deals:
                    h = deal_hash(d)
                    if h not in sent_hashes:
                        d["_hash"] = h
                        all_new.append((d, False))  # (deal, is_round_trip)

            await asyncio.sleep(0.2)  # Rate limit
    else:
        # Round-trip mode
        for month_str in sorted(months_to_fetch):
            # Outbound
            outbound_tickets = await fetch_flights(month_str, origin, destination, cfg, settings)
            # Return
            return_tickets = await fetch_flights(month_str, destination, origin, cfg, settings)

            # Build combinations for each day
            outbound_by_date = {}
            for ticket in outbound_tickets:
                dep_date_str = ticket.get("departure_at", "").split("T")[0]
                if not dep_date_str:
                    continue
                try:
                    dep_date = datetime.fromisoformat(dep_date_str).date()
                except Exception:
                    continue

                if dep_date < today or dep_date > end_date:
                    continue

                deals = filter_deals([ticket], settings)
                if deals:
                    if dep_date not in outbound_by_date:
                        outbound_by_date[dep_date] = []
                    outbound_by_date[dep_date].extend(deals)

            return_by_date = {}
            for ticket in return_tickets:
                dep_date_str = ticket.get("departure_at", "").split("T")[0]
                if not dep_date_str:
                    continue
                try:
                    dep_date = datetime.fromisoformat(dep_date_str).date()
                except Exception:
                    continue

                if dep_date < today or dep_date > end_date:
                    continue

                deals = filter_deals([ticket], settings)
                if deals:
                    if dep_date not in return_by_date:
                        return_by_date[dep_date] = []
                    return_by_date[dep_date].extend(deals)

            # Combine outbound and return
            limit_per_segment = settings["base_price_eur"]
            for out_date in outbound_by_date:
                for ret_date in return_by_date:
                    for out_deal in outbound_by_date[out_date]:
                        for ret_deal in return_by_date[ret_date]:
                            total_price = out_deal.get("price", 0) + ret_deal.get("price", 0)
                            if total_price <= 2 * limit_per_segment:
                                combo = {
                                    "_outbound": out_deal,
                                    "_return": ret_deal,
                                    "_total_price": total_price,
                                }
                                h = deal_hash(combo)
                                if h not in sent_hashes:
                                    combo["_hash"] = h
                                    all_new.append((combo, True))  # (deal, is_round_trip)

            await asyncio.sleep(0.2)  # Rate limit

    # Send new deals
    if all_new:
        # Sort by price
        all_new.sort(key=lambda x: x[0].get("_total_price" if x[1] else "price", 999999))

        text = f"🎉 Found {len(all_new)} new deal(s)!\n\n"
        for deal, is_round_trip in all_new:
            text += format_deal(deal, is_round_trip) + "\n"

        await send_tg(text, chat_id, cfg)

        # Update sent deals
        user_data["sent_deals"] = list(sent_hashes | {d[0].get("_hash") for d in all_new})
        state["users"][str(chat_id)] = user_data
    elif notify_if_empty:
        logger.debug(f"ℹ️ No new flights found for user {chat_id}")
        await send_tg("ℹ️ Новых полётов не найдено.", chat_id, cfg)
    else:
        logger.debug(f"ℹ️ No new flights found for user {chat_id} (silent mode)")


async def hourly_flight_check(cfg: dict, state: dict) -> None:
    """Background task: check flights for all users every hour."""
    while True:
        print(f"🔍 Hourly check for {len(state['users'])} user(s)…")
        logger.info(f"🔍 Hourly flight check starting for {len(state['users'])} user(s)")
        for chat_id in list(state["users"].keys()):
            try:
                logger.debug(f"  Checking user {chat_id}...")
                await search_for_user(chat_id, state, cfg)
                logger.debug(f"  ✅ Completed for {chat_id}")
            except Exception as e:
                print(f"  ❌ Error for {chat_id}: {e}")
                logger.error(f"  ❌ Error for {chat_id}: {e}", exc_info=True)

        save_state(state, cfg)
        logger.info(f"✅ Hourly check completed, sleeping for 60 seconds...")
        await asyncio.sleep(60)  # TEST: 60 seconds (normally 3600 for 1 hour)


# ---------------------------------------------------------------------------
# Telegram Commands
# ---------------------------------------------------------------------------

async def process_single_update(update: dict, cfg: dict, state: dict) -> None:
    """Process a single Telegram update."""
    logger.debug(f"🔍 process_single_update called, update keys: {update.keys()}")

    # Extract message and user info
    message = update.get("message")
    if not message:
        logger.debug("⚠️ No message in update, returning")
        return

    chat_id = str(message.get("chat", {}).get("id", ""))
    user = message.get("from", {})
    user_id = str(user.get("id", ""))
    username = user.get("username", "")
    first_name = user.get("first_name", "")

    text = (message.get("text", "") or "").strip()
    logger.debug(f"📝 Message from {chat_id} ({first_name}/@{username}): '{text}'")

    if not text:
        logger.debug("⚠️ Empty message text, returning")
        return

    # Parse command
    cmd_parts = text.split(None, 1)
    cmd = cmd_parts[0].lower()
    arg = cmd_parts[1] if len(cmd_parts) > 1 else ""
    logger.debug(f"🎯 Parsed command: cmd='{cmd}', arg='{arg}'")

    admin_id = cfg.get("admin_chat_id", "")
    is_admin = str(chat_id) == admin_id
    logger.debug(f"👤 User status: admin={is_admin}, admin_id={admin_id}")

    # Check if user is revoked
    if str(chat_id) in state["revoked"]:
        logger.debug(f"🚫 User {chat_id} is revoked")
        if cmd == "/start":
            await send_tg("⛔ Ваш доступ был отозван.", chat_id, cfg)
        return

    # Check if user is approved
    is_approved = str(chat_id) in state["users"]
    logger.debug(f"✅ User approval status: approved={is_approved}, total_users={len(state['users'])}")

    # Pending users
    if not is_approved and str(chat_id) in state["pending"]:
        pending_user = state["pending"][str(chat_id)]

        if pending_user.get("state") == "awaiting_referral":
            # Non-command message = referral answer
            if not text.startswith("/"):
                pending_user["referral_answer"] = text
                pending_user["state"] = "awaiting_approval"
                state["pending"][str(chat_id)] = pending_user

                await send_tg(
                    "📨 Спасибо! Запрос отправлен. Ожидайте одобрения.",
                    chat_id, cfg
                )

                # Notify admin
                admin_msg = (
                    f"🆕 Новый запрос от *{first_name}* (@{username})\n"
                    f"ID: `{chat_id}`\n"
                    f"Откуда узнал: «{text}»\n\n"
                    f"/approve {chat_id} или /reject {chat_id}"
                )
                await send_tg(admin_msg, admin_id, cfg)
                return
            elif cmd in ["/start", "/help"]:
                await send_tg(
                    "Пожалуйста, ответьте на вопрос выше.",
                    chat_id, cfg
                )
                return

        elif pending_user.get("state") == "awaiting_approval":
            if cmd == "/start":
                await send_tg(
                    "Запрос отправлен, ожидайте.",
                    chat_id, cfg
                )
            return

    # New user
    if not is_approved and cmd == "/start":
        state["pending"][str(chat_id)] = {
            "name": first_name,
            "username": username,
            "state": "awaiting_referral",
        }
        await send_tg(
            "Откуда вы узнали про бота? Поделитесь, пожалуйста 🙏",
            chat_id, cfg
        )
        return

    # Only approved users and admins beyond this point
    if not is_approved and not is_admin:
        await send_tg(
            "⛔ У вас нет доступа.",
            chat_id, cfg
        )
        return

    # Admin commands
    if is_admin:
        if cmd == "/approve":
            target_id = arg.strip()
            if target_id in state["pending"]:
                pending = state["pending"].pop(target_id)
                state["users"][target_id] = {
                    "name": pending.get("name", ""),
                    "username": pending.get("username", ""),
                    "referral_answer": pending.get("referral_answer", ""),
                    "settings": DEFAULT_USER_SETTINGS.copy(),
                    "sent_deals": [],
                }
                await send_tg(f"✅ Пользователь {pending['name']} одобрен.", chat_id, cfg)
                await send_tg("✅ Вас одобрили! /help для справки.", target_id, cfg)
            return

        elif cmd == "/reject":
            target_id = arg.strip()
            if target_id in state["pending"]:
                pending = state["pending"].pop(target_id)
                await send_tg(f"❌ Пользователь {pending['name']} отклонен.", chat_id, cfg)
                await send_tg("❌ В доступе отказано.", target_id, cfg)
            return

        elif cmd == "/revoke":
            target_id = arg.strip()
            if target_id in state["users"]:
                user_info = state["users"].pop(target_id)
                state["revoked"][target_id] = {
                    "name": user_info.get("name", ""),
                    "username": user_info.get("username", ""),
                }
                await send_tg(f"✅ Пользователь {user_info['name']} удалён.", chat_id, cfg)
                await send_tg("⛔ Ваш доступ отозван администратором.", target_id, cfg)
            return

        elif cmd == "/users":
            if not state["users"]:
                await send_tg("Пользователей нет.", chat_id, cfg)
                return

            text = "👥 *Пользователи:*\n\n"
            for uid, udata in sorted(state["users"].items()):
                s = user_settings(state, uid)
                dest = s.get("destination", "")
                trip_type = f"туда-обратно ({dest})" if dest else "в одну сторону"
                text += (
                    f"*{udata['name']}* (@{udata.get('username', 'N/A')})\n"
                    f"  ID: `{uid}`\n"
                    f"  🔄 Тип: {trip_type}\n"
                    f"  ✈️ {s['origin']} → ? | Цена: {s['base_price_eur']}€\n"
                    f"  📅 Дней: {s['days_ahead']} | Сделок: {len(udata.get('sent_deals', []))}\n\n"
                )

            if state["pending"]:
                text += "⏳ *Ожидают одобрения:*\n\n"
                for uid, pdata in sorted(state["pending"].items()):
                    text += f"  {pdata['name']} (@{pdata.get('username', 'N/A')}) — `{uid}`\n"

            await send_tg(text, chat_id, cfg)
            return

        elif cmd == "/userlist":
            if not state["users"]:
                await send_tg("Пользователей нет.", chat_id, cfg)
                return

            user_ids = "\n".join(sorted(state["users"].keys()))
            await send_tg(
                f"📋 Все ID пользователей:\n\n{user_ids}\n\nВсего: {len(state['users'])}",
                chat_id, cfg
            )
            return

        elif cmd == "/require":
            if arg.strip().lower() == "all":
                logger.debug(f"🔄 /require all - admin requesting flight search for ALL users")
                await send_tg("🔄 Ищу полёты для всех пользователей...", chat_id, cfg)
                for user_id in list(state["users"].keys()):
                    try:
                        logger.debug(f"  Checking user {user_id}...")
                        await search_for_user(user_id, state, cfg, notify_if_empty=True)
                    except Exception as e:
                        logger.error(f"  ❌ Error for {user_id}: {e}")
                await send_tg("✅ Поиск завершён для всех!", chat_id, cfg)
                logger.debug(f"✅ /require all completed")
            else:
                logger.debug(f"🔄 /require - admin requesting flight search for themselves")
                await send_tg("🔄 Поиск полётов...", chat_id, cfg)
                await search_for_user(chat_id, state, cfg, notify_if_empty=True)
                await send_tg("✅ Поиск завершён!", chat_id, cfg)
                logger.debug(f"✅ /require completed for admin")
            return

        elif cmd == "/write":
            if not arg.strip():
                await send_tg("⚠️ Укажите сообщение: /write сообщение", chat_id, cfg)
                return

            message = arg.strip()
            logger.debug(f"📢 /write - sending message to all users: {message[:50]}")
            await send_tg(f"✅ Отправляю сообщение {len(state['users'])} пользователям...", chat_id, cfg)

            sent_count = 0
            for user_id in list(state["users"].keys()):
                try:
                    logger.debug(f"  Sending to {user_id}...")
                    result = await send_tg(message, user_id, cfg)
                    if result:
                        sent_count += 1
                except Exception as e:
                    logger.error(f"  ❌ Error sending to {user_id}: {e}")

            await send_tg(f"✅ Отправлено {sent_count}/{len(state['users'])} пользователям!", chat_id, cfg)
            logger.debug(f"✅ /write completed")
            return

    # User commands (approved users)
    logger.debug(f"🎮 Checking user commands for: {cmd}")

    if cmd == "/start" or cmd == "/help":
        logger.debug(f"📖 Executing /help command for user {chat_id}")
        await send_tg(HELP_TEXT, chat_id, cfg)
        logger.debug(f"✅ /help sent to {chat_id}")
        return

    if cmd == "/settings":
        logger.debug(f"⚙️ Executing /settings command for user {chat_id}")
        s = user_settings(state, chat_id)
        logger.debug(f"📊 User settings loaded: {json.dumps(s)}")
        dest = s.get("destination", "")
        trip_type = f"туда-обратно ({dest})" if dest else "в одну сторону"

        text = (
            f"⚙️ *Ваши настройки:*\n\n"
            f"✈️ Город вылета: `{s['origin']}` `/origin`\n"
            f"🔄 Тип: {trip_type} `/destination`\n"
            f"📅 Дней вперёд: {s['days_ahead']} `/days`\n"
            f"💰 Базовая цена: {s['base_price_eur']}€ `/price`\n"
            f"⏱️ Макс. длина за базовую цену: {s['base_duration_minutes']}мин `/duration`\n"
            f"📈 Доп. €/30мин: {s['price_increment_eur']}€ `/increment`\n"
            f"🎯 Только прямые: {'✅' if s['direct_only'] else '❌'} `/direct`\n"
        )

        logger.debug(f"📨 Sending settings message to {chat_id}")
        await send_tg(text, chat_id, cfg)
        logger.debug(f"✅ /settings sent to {chat_id}")
        return

    if cmd == "/reset":
        logger.debug(f"🔄 Executing /reset command for user {chat_id}")
        state["users"][str(chat_id)]["sent_deals"] = []
        await send_tg("✅ История отправленных сделок очищена.", chat_id, cfg)
        logger.debug(f"✅ /reset executed for {chat_id}")
        return

    if cmd == "/direct":
        logger.debug(f"🎯 Executing /direct command for user {chat_id}")
        settings = user_settings(state, chat_id)
        settings["direct_only"] = not settings.get("direct_only", False)
        state["users"][str(chat_id)]["settings"] = settings
        status = "✅ включены" if settings["direct_only"] else "❌ отключены"
        await send_tg(f"Только прямые рейсы: {status}", chat_id, cfg)
        logger.debug(f"✅ /direct executed for {chat_id}")
        return

    # Settings commands
    if cmd in COMMAND_MAP:
        logger.debug(f"⚙️ Processing COMMAND_MAP command: {cmd} = {arg}")
        key, vtype = COMMAND_MAP[cmd]
        logger.debug(f"📝 Command mapped to key='{key}', vtype={vtype}")

        if not arg:
            logger.debug(f"❌ No argument provided for {cmd}")
            await send_tg(f"⚠️ Укажите значение: {cmd} <значение>", chat_id, cfg)
            return

        try:
            if key == "destination":
                # Special handling for destination
                if arg.lower() == "off" or arg == "":
                    val = ""
                    logger.debug(f"🔄 Clearing destination")
                else:
                    val = arg.upper()
                    if len(val) != 3:
                        logger.debug(f"❌ Invalid destination code length: {val}")
                        await send_tg("⚠️ Код назначения должен быть 3 буквы (IATA)", chat_id, cfg)
                        return
                    logger.debug(f"🔄 Setting destination to {val}")

            elif key == "days_ahead":
                val = int(arg)
                if val < 1 or val > 90:
                    logger.debug(f"❌ days_ahead out of range: {val}")
                    await send_tg("⚠️ /days должна быть от 1 до 90", chat_id, cfg)
                    return
                logger.debug(f"🔄 Setting days_ahead to {val}")

            else:
                val = vtype(arg)
                logger.debug(f"🔄 Setting {key} to {val}")

            settings = user_settings(state, chat_id)
            settings[key] = val
            state["users"][str(chat_id)]["settings"] = settings
            logger.debug(f"💾 Settings saved for user {chat_id}: {key}={val}")

            if key == "destination":
                if val:
                    await send_tg(f"✅ Город назначения: {val} (туда-обратно)", chat_id, cfg)
                else:
                    await send_tg(f"✅ Туда-обратно отключено (только в одну сторону)", chat_id, cfg)
            else:
                await send_tg(f"✅ {cmd} установлена на {val}", chat_id, cfg)
            logger.debug(f"✅ Confirmation sent to {chat_id}")

        except ValueError as e:
            logger.error(f"❌ ValueError for {cmd}: {e}")
            await send_tg(f"⚠️ Неверное значение для {cmd}", chat_id, cfg)

        return

    # Unknown command
    logger.debug(f"❓ Unknown command received: {cmd}")
    await send_tg(
        "❓ Неизвестная команда. /help для справки.",
        chat_id, cfg
    )
    logger.debug(f"✅ Unknown command message sent to {chat_id}")


# ---------------------------------------------------------------------------
# Webhook Handler
# ---------------------------------------------------------------------------

async def handle_webhook(request):
    """Handle incoming Telegram webhook updates."""
    logger.debug("🔔 Webhook received")
    try:
        data = await request.json()
        logger.debug(f"📨 Raw webhook data: {json.dumps(data)}")

        # We need to pass state and cfg somehow
        # Store them in app context
        state = request.app["state"]
        cfg = request.app["cfg"]

        logger.debug(f"⚙️ Calling process_single_update with data keys: {data.keys()}")
        await process_single_update(data, cfg, state)
        logger.debug("✅ process_single_update completed")
    except Exception as e:
        logger.error(f"❌ Webhook handler error: {e}", exc_info=True)

    return web.Response(text="ok")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    """Main entry point."""
    global session

    logger.info("🚀 Starting Flight Deals bot")
    cfg = load_config()
    logger.info(f"⚙️ Config loaded: webhook_port={cfg.get('webhook_port')}, listen_port={cfg.get('listen_port')}")
    logger.info(f"📋 Full config keys: {list(cfg.keys())}")
    logger.debug(f"🔧 Full config: {json.dumps({k: v for k, v in cfg.items() if k != 'telegram_bot_token'})}")

    state = load_state(cfg)
    logger.info(f"📂 State loaded: {len(state.get('users', {}))} users")

    # Create HTTP session
    session = ClientSession()
    logger.debug("📡 HTTP session created")

    # Set webhook
    logger.debug("🔌 Setting webhook...")
    await set_webhook(cfg)

    # Create web app
    app = web.Application()
    app["state"] = state
    app["cfg"] = cfg
    app.router.add_post(cfg["webhook_path"], handle_webhook)

    # Start web server
    logger.info(f"🌐 Starting web server on port {cfg['listen_port']}")
    logger.debug(f"📍 Before TCPSite: listen_port type={type(cfg['listen_port'])}, value={cfg['listen_port']}")

    runner = web.AppRunner(app)
    await runner.setup()
    logger.debug("✅ AppRunner setup complete")

    listen_port = cfg["listen_port"]
    logger.info(f"🔗 Creating TCPSite on 0.0.0.0:{listen_port}")
    site = web.TCPSite(runner, "0.0.0.0", listen_port)
    logger.debug("⏳ Calling site.start()...")
    await site.start()
    logger.info(f"✅ Webhook server listening on 0.0.0.0:{listen_port}")
    logger.info("🔴 AFTER site.start() - about to create task")
    import sys; sys.stdout.flush()

    # Start background flight check task
    logger.info("🔴 Creating hourly_flight_check task...")
    asyncio.create_task(hourly_flight_check(cfg, state))
    logger.info("🔴 Task created")
    print("✅ Hourly flight check started")
    sys.stdout.flush()

    # Keep running
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("🛑 Shutting down...")
        await session.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
