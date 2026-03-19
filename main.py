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

__version__ = "2.1"

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
# Airport coordinates (IATA → (lat, lon)) for distance-based pricing
# Covers the most common European + global routes. Unknown airports fall back
# to the user's configured base_duration_minutes.
# ---------------------------------------------------------------------------

AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    # Hungary
    "BUD": (47.43, 19.26),
    # UK
    "LHR": (51.48, -0.45), "LGW": (51.15, -0.18), "STN": (51.89, 0.24),
    "LTN": (51.87, -0.37), "MAN": (53.35, -2.27), "EDI": (55.95, -3.37),
    "BHX": (52.45, -1.74), "BRS": (51.38, -2.72),
    # Germany
    "FRA": (50.03, 8.57), "MUC": (48.35, 11.79), "BER": (52.36, 13.50),
    "DUS": (51.29, 6.77), "HAM": (53.63, 10.00), "STR": (48.69, 9.22),
    "CGN": (50.87, 7.14),
    # France
    "CDG": (49.01, 2.55), "ORY": (48.72, 2.36), "NCE": (43.66, 7.21),
    "LYS": (45.72, 5.08), "MRS": (43.44, 5.22),
    # Spain
    "MAD": (40.47, -3.56), "BCN": (41.30, 2.08), "AGP": (36.67, -4.50),
    "PMI": (39.55, 2.74), "VLC": (39.49, -0.48), "SVQ": (37.42, -5.89),
    "LPA": (27.93, -15.39), "TFS": (28.04, -16.57),
    # Italy
    "FCO": (41.80, 12.24), "MXP": (45.63, 8.72), "LIN": (45.45, 9.28),
    "VCE": (45.51, 12.35), "NAP": (40.88, 14.29), "BLQ": (44.53, 11.29),
    "CTA": (37.47, 15.07), "PMO": (38.18, 13.09),
    # Netherlands
    "AMS": (52.31, 4.77),
    # Belgium
    "BRU": (50.90, 4.48),
    # Austria
    "VIE": (48.11, 16.57),
    # Switzerland
    "ZRH": (47.46, 8.55), "GVA": (46.24, 6.11),
    # Czech Republic
    "PRG": (50.10, 14.26),
    # Poland
    "WAW": (52.17, 20.97), "KRK": (50.08, 19.78), "WRO": (51.10, 16.89),
    # Romania
    "OTP": (44.57, 26.10), "CLJ": (46.79, 23.69),
    # Greece
    "ATH": (37.94, 23.95), "SKG": (40.52, 22.97), "HER": (35.34, 25.18),
    "RHO": (36.41, 28.09), "CFU": (39.60, 19.91),
    # Turkey
    "IST": (41.27, 28.75), "SAW": (40.90, 29.31), "AYT": (36.90, 30.80),
    "ESB": (40.13, 32.99),
    # Portugal
    "LIS": (38.77, -9.13), "OPO": (41.24, -8.68), "FAO": (37.01, -7.97),
    # Croatia
    "ZAG": (45.74, 16.07), "SPU": (43.54, 16.30), "DBV": (42.56, 18.27),
    # Serbia
    "BEG": (44.82, 20.31),
    # Ukraine / Eastern Europe
    "KBP": (50.34, 30.89), "LWO": (49.81, 23.95),
    # Scandinavia
    "CPH": (55.62, 12.66), "OSL": (60.19, 11.10), "ARN": (59.65, 17.92),
    "HEL": (60.32, 24.96),
    # Middle East
    "DXB": (25.25, 55.36), "AUH": (24.43, 54.65), "DOH": (25.27, 51.61),
    "TLV": (31.99, 34.79), "AMM": (31.72, 35.99), "BEY": (33.82, 35.49),
    # Asia
    "BKK": (13.69, 100.75), "HKT": (8.11, 98.32), "DMK": (13.91, 100.61),
    "SIN": (1.36, 103.99), "KUL": (2.74, 101.70), "HKG": (22.31, 113.91),
    "NRT": (35.77, 140.39), "ICN": (37.46, 126.44), "PEK": (40.08, 116.58),
    "DEL": (28.56, 77.10), "BOM": (19.09, 72.87),
    # Americas
    "JFK": (40.64, -73.78), "EWR": (40.69, -74.17), "LAX": (33.94, -118.40),
    "MIA": (25.79, -80.29), "ORD": (41.97, -87.91), "YYZ": (43.68, -79.63),
    "GRU": (23.43, -46.47), "EZE": (-34.82, -58.54), "BOG": (4.70, -74.15),
    "CUN": (21.04, -86.87),
    # Africa
    "CAI": (30.11, 31.41), "CMN": (33.37, -7.58), "CPT": (-33.96, 18.60),
    "JNB": (-26.14, 28.24), "NBO": (-1.32, 36.93), "TUN": (36.85, 10.23),
    # Australia
    "SYD": (-33.94, 151.18), "MEL": (-37.67, 144.84),
}

# Average cruise speed used to estimate flight time from distance
_AVG_SPEED_KMH = 850.0
# Minimum estimated duration floor (even very short routes take at least 45 min)
_MIN_ESTIMATED_MINUTES = 45


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in km."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def estimated_flight_minutes(origin: str, destination: str) -> int | None:
    """Estimate direct flight duration in minutes from airport coordinates.

    Returns None if either airport is not in the known coordinates dict,
    in which case callers should fall back to user's base_duration_minutes.
    """
    if origin not in AIRPORT_COORDS or destination not in AIRPORT_COORDS:
        return None
    lat1, lon1 = AIRPORT_COORDS[origin]
    lat2, lon2 = AIRPORT_COORDS[destination]
    km = _haversine_km(lat1, lon1, lat2, lon2)
    minutes = int((km / _AVG_SPEED_KMH) * 60)
    return max(minutes, _MIN_ESTIMATED_MINUTES)


# ---------------------------------------------------------------------------
# Constants & Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path("/app/data") / "state.json"

TG_API = "https://api.telegram.org/bot{token}"
AVIASALES_API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

HELP_TEXT = """🤖 *Команды:*

/origin XXX — город вылета (IATA)
/destination XXX — город назначения (IATA, для туда-обратно)
/destination ANY — туда-обратно везде (любое направление)
/destination off — отключить туда-обратно (только в одну сторону)
/days N — окно поиска (дней вперёд, 1–90)
/price N — лимит цены за сегмент (€)
/duration N — макс. длительность для базовой цены (мин)
/increment N — доп. € за каждые 30 мин
/direct — вкл/выкл только прямые
/tripdays MIN MAX — мин-макс дней между вылетами (туда-обратно), напр. /tripdays 3 10
/settings — текущие настройки
/reset — сбросить историю отправленных
/savepreset NAME — сохранить текущие настройки как пресет
/loadpreset NAME — загрузить пресет
/deletepreset NAME — удалить пресет
/mypresets — список пресетов
/help — справка"""

ADMIN_HELP = """
👑 *Админ-команды:*
/approve ID — одобрить пользователя
/reject ID — отклонить
/revoke ID — удалить пользователя (бан)
/users — список пользователей
/userlist — все ID пользователей
/approval on|off — вкл/выкл обязательное одобрение
/announce текст — отправить объявление всем пользователям (Markdown)
/analytics — ссылка на дашборд аналитики"""

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
    "min_trip_days": 1,   # min days between outbound and return (round-trip only)
    "max_trip_days": 30,  # max days between outbound and return (round-trip only)
}

COMMAND_MAP = {
    "/origin": ("origin", str),
    "/destination": ("destination", str),
    "/days": ("days_ahead", int),
    "/price": ("base_price_eur", int),
    "/duration": ("base_duration_minutes", int),
    "/increment": ("price_increment_eur", int),
    "/tripdays": ("trip_days_range", str),  # handled specially: "MIN-MAX" or "MIN MAX"
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
        "approval_required": True,
        "analytics": {"daily": {}},
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


def _analytics_day(state: dict) -> dict:
    """Return today's analytics bucket, creating it if needed."""
    state.setdefault("analytics", {"daily": {}})
    today = datetime.utcnow().strftime("%Y-%m-%d")
    day = state["analytics"]["daily"].setdefault(today, {
        "joins": 0,
        "deals_sent": 0,
        "origins": {},
        "destinations": {},
    })
    return day


def record_join(state: dict) -> None:
    """Increment today's join counter."""
    _analytics_day(state)["joins"] += 1


def record_deals_sent(state: dict, deals: list, settings: dict) -> None:
    """Record sent deals into today's analytics bucket."""
    day = _analytics_day(state)
    day["deals_sent"] += len(deals)
    origin = settings.get("origin", "")
    dest = settings.get("destination", "")
    if origin:
        day["origins"][origin] = day["origins"].get(origin, 0) + len(deals)
    dest_label = dest if dest else "one-way"
    day["destinations"][dest_label] = day["destinations"].get(dest_label, 0) + len(deals)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def user_settings(state: dict, chat_id: str) -> dict:
    """Get user settings with defaults."""
    user = state["users"].get(str(chat_id), {})
    settings = user.get("settings", {})
    return {**DEFAULT_USER_SETTINGS, **settings}


def trip_type_label(dest: str) -> str:
    """Human-readable trip type for a destination value."""
    if dest == "ANY":
        return "туда-обратно (везде)"
    elif dest:
        return f"туда-обратно ({dest})"
    return "в одну сторону"


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
    """Filter deals based on user settings.

    For connecting flights, the price threshold is based on the estimated
    direct flight duration (from airport distance), not the actual travel time.
    This prevents a 10h connecting flight from being allowed at a higher price
    than a 2h direct flight on the same route.
    """
    result = []
    for ticket in tickets:
        # Extract price and duration
        price = ticket.get("price")
        duration = ticket.get("duration", 0)

        if price is None:
            continue

        # Use estimated direct flight time as the price baseline for connecting flights.
        # This ensures a BUD→LON flight with 2 stops is judged against the ~2.5h direct
        # baseline, not its actual 8h travel time.
        origin = ticket.get("origin", "")
        destination = ticket.get("destination", "")
        estimated = estimated_flight_minutes(origin, destination)
        baseline_duration = estimated if estimated is not None else settings["base_duration_minutes"]

        # Check price vs max price for this route's estimated duration
        max_p = max_price_for_duration(baseline_duration, settings)
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
        ret_url = ret.get("search_url", "") or out_url  # fallback to outbound URL if no return URL

        msg += f"  ← {dest} → {origin}\n"
        msg += f"    {ret_date} {ret_time} | {ret_duration_str} | {ret_direct}\n"
        msg += f"    💰 {ret_price} EUR\n"
        if ret_airline and ret_flight:
            msg += f"    {ret_airline} {ret_flight}\n"
        if ret_url:
            msg += f"[link]({ret_url}) · by aboutmisha.com\n"
        else:
            msg += f"by aboutmisha.com\n"

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
            msg += f"[link]({url}) · by aboutmisha.com\n"
        else:
            msg += f"by aboutmisha.com\n"

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

    if destination == "ANY":
        # Everywhere round-trip mode:
        # 1. Fetch outbound tickets with no destination filter
        # 2. For each unique destination found, fetch return tickets
        # 3. Combine date pairs within trip days range
        min_trip = settings.get("min_trip_days", 1)
        max_trip = settings.get("max_trip_days", 30)
        limit_per_segment = settings["base_price_eur"]
        MAX_DESTINATIONS = 20  # cap API calls

        for month_str in sorted(months_to_fetch):
            outbound_tickets = await fetch_flights(month_str, origin, "", cfg, settings)

            # Group filtered outbound tickets by destination and date
            outbound_by_dest_date: dict = {}
            for ticket in outbound_tickets:
                dep_date_str = ticket.get("departure_at", "").split("T")[0]
                dest_city = ticket.get("destination", "")
                if not dep_date_str or not dest_city:
                    continue
                try:
                    dep_date = datetime.fromisoformat(dep_date_str).date()
                except Exception:
                    continue
                if dep_date < today or dep_date > end_date:
                    continue
                deals = filter_deals([ticket], settings)
                if deals:
                    key = (dest_city, dep_date)
                    outbound_by_dest_date.setdefault(key, []).extend(deals)

            # Collect unique destinations (cheapest first)
            dest_min_price: dict = {}
            for (dest_city, dep_date), deals in outbound_by_dest_date.items():
                min_p = min(d.get("price", 9999) for d in deals)
                if dest_city not in dest_min_price or min_p < dest_min_price[dest_city]:
                    dest_min_price[dest_city] = min_p
            top_dests = sorted(dest_min_price, key=lambda d: dest_min_price[d])[:MAX_DESTINATIONS]

            # Fetch return legs for each top destination
            for dest_city in top_dests:
                return_tickets = await fetch_flights(month_str, dest_city, origin, cfg, settings)
                await asyncio.sleep(0.2)

                return_by_date: dict = {}
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
                        return_by_date.setdefault(dep_date, []).extend(deals)

                # Combine
                for (d_city, out_date), out_deals in outbound_by_dest_date.items():
                    if d_city != dest_city:
                        continue
                    for ret_date, ret_deals in return_by_date.items():
                        trip_length = (ret_date - out_date).days
                        if trip_length < min_trip or trip_length > max_trip:
                            continue
                        for out_deal in out_deals:
                            for ret_deal in ret_deals:
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
                                        all_new.append((combo, True))

            await asyncio.sleep(0.2)

    elif not destination:
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
            min_trip = settings.get("min_trip_days", 1)
            max_trip = settings.get("max_trip_days", 30)
            for out_date in outbound_by_date:
                for ret_date in return_by_date:
                    # Enforce layover range
                    trip_length = (ret_date - out_date).days
                    if trip_length < min_trip or trip_length > max_trip:
                        continue
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

        record_deals_sent(state, all_new, settings)

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
        logger.info(f"✅ Hourly check completed, sleeping for 3600 seconds...")
        await asyncio.sleep(3600)


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
        if not state.get("approval_required", True):
            # Open access: auto-approve immediately
            state["users"][str(chat_id)] = {
                "name": first_name,
                "username": username,
                "referral_answer": "",
                "settings": DEFAULT_USER_SETTINGS.copy(),
                "sent_deals": [],
                "joined_at": datetime.utcnow().strftime("%Y-%m-%d"),
            }
            record_join(state)
            save_state(state, cfg)
            await send_tg("✅ Добро пожаловать! /help для справки.", chat_id, cfg)
        else:
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
                    "joined_at": datetime.utcnow().strftime("%Y-%m-%d"),
                }
                record_join(state)
                save_state(state, cfg)
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
                trip_type = trip_type_label(dest)
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

        elif cmd == "/approval":
            val = arg.strip().lower()
            if val == "off":
                state["approval_required"] = False
                save_state(state, cfg)
                await send_tg("🔓 Одобрение отключено — любой может вступить.", chat_id, cfg)
            elif val == "on":
                state["approval_required"] = True
                save_state(state, cfg)
                await send_tg("🔒 Одобрение включено — новые пользователи требуют проверки.", chat_id, cfg)
            else:
                status = "🔒 включено" if state.get("approval_required", True) else "🔓 отключено"
                await send_tg(f"Одобрение сейчас: {status}\n\n/approval on — включить\n/approval off — отключить", chat_id, cfg)
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

        elif cmd == "/announce":
            if not arg.strip():
                await send_tg("⚠️ Укажите сообщение: /announce текст (поддерживает Markdown)", chat_id, cfg)
                return
            message = arg.strip()
            await send_tg(f"📢 Отправляю объявление {len(state['users'])} пользователям...", chat_id, cfg)
            sent_count = 0
            for user_id in list(state["users"].keys()):
                try:
                    result = await send_tg(message, user_id, cfg)
                    if result:
                        sent_count += 1
                except Exception as e:
                    logger.error(f"  ❌ Error announcing to {user_id}: {e}")
            await send_tg(f"✅ Объявление отправлено {sent_count}/{len(state['users'])} пользователям!", chat_id, cfg)
            return

        elif cmd == "/analytics":
            token = analytics_token(cfg)
            url = f"{cfg.get('webhook_host', '')}/flights/analytics?token={token}"
            await send_tg(f"📊 [Analytics]({url})", chat_id, cfg)
            return

    # User commands (approved users)
    logger.debug(f"🎮 Checking user commands for: {cmd}")

    if cmd == "/start" or cmd == "/help":
        logger.debug(f"📖 Executing /help command for user {chat_id}")
        msg = HELP_TEXT
        if is_admin:
            msg += "\n" + ADMIN_HELP
        await send_tg(msg, chat_id, cfg)
        logger.debug(f"✅ /help sent to {chat_id}")
        return

    if cmd == "/settings":
        logger.debug(f"⚙️ Executing /settings command for user {chat_id}")
        s = user_settings(state, chat_id)
        logger.debug(f"📊 User settings loaded: {json.dumps(s)}")
        dest = s.get("destination", "")
        trip_type = trip_type_label(dest)

        text = (
            f"⚙️ *Ваши настройки:*\n\n"
            f"✈️ Город вылета: `{s['origin']}` `/origin`\n"
            f"🔄 Тип: {trip_type} `/destination`\n"
            f"📅 Дней вперёд: {s['days_ahead']} `/days`\n"
            f"💰 Базовая цена: {s['base_price_eur']}€ `/price`\n"
            f"⏱️ Макс. длина за базовую цену: {s['base_duration_minutes']}мин `/duration`\n"
            f"📈 Доп. €/30мин: {s['price_increment_eur']}€ `/increment`\n"
            f"🎯 Только прямые: {'✅' if s['direct_only'] else '❌'} `/direct`\n"
            f"📆 Длительность поездки: {s.get('min_trip_days', 1)}–{s.get('max_trip_days', 30)} дней `/tripdays`\n"
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

    if cmd == "/tripdays":
        parts = arg.strip().replace("-", " ").split()
        if len(parts) != 2:
            await send_tg("⚠️ Укажите мин и макс: /tripdays MIN MAX (напр. /tripdays 3 10)", chat_id, cfg)
            return
        try:
            min_days, max_days = int(parts[0]), int(parts[1])
        except ValueError:
            await send_tg("⚠️ Значения должны быть целыми числами.", chat_id, cfg)
            return
        if min_days < 1 or max_days < min_days or max_days > 365:
            await send_tg("⚠️ Допустимый диапазон: мин ≥ 1, макс ≥ мин, макс ≤ 365.", chat_id, cfg)
            return
        settings = user_settings(state, chat_id)
        settings["min_trip_days"] = min_days
        settings["max_trip_days"] = max_days
        state["users"][str(chat_id)]["settings"] = settings
        save_state(state, cfg)
        await send_tg(f"✅ Длительность поездки: {min_days}–{max_days} дней.", chat_id, cfg)
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
                elif arg.upper() == "ANY":
                    val = "ANY"
                    logger.debug(f"🔄 Setting destination to ANY (everywhere round-trip)")
                else:
                    val = arg.upper()
                    if len(val) != 3:
                        logger.debug(f"❌ Invalid destination code length: {val}")
                        await send_tg("⚠️ Код: 3 буквы IATA, 'ANY' для везде, 'off' для отключить", chat_id, cfg)
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
                if val == "ANY":
                    await send_tg("✅ Режим: везде (туда-обратно, любое направление)", chat_id, cfg)
                elif val:
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

    MAX_PRESETS = 5

    if cmd == "/savepreset":
        name = arg.strip()
        if not name:
            await send_tg("⚠️ Укажите название: /savepreset NAME", chat_id, cfg)
            return
        if not name.replace("_", "").replace("-", "").isalnum():
            await send_tg("⚠️ Название может содержать только буквы, цифры, - и _", chat_id, cfg)
            return
        user_data = state["users"][str(chat_id)]
        presets = user_data.setdefault("presets", {})
        if name not in presets and len(presets) >= MAX_PRESETS:
            names = ", ".join(f"`{n}`" for n in presets)
            await send_tg(
                f"⚠️ Максимум {MAX_PRESETS} пресетов. Удалите один:\n{names}\n\n/deletepreset NAME",
                chat_id, cfg
            )
            return
        presets[name] = user_settings(state, chat_id).copy()
        save_state(state, cfg)
        await send_tg(f"✅ Пресет `{name}` сохранён.", chat_id, cfg)
        return

    if cmd == "/loadpreset":
        name = arg.strip()
        if not name:
            await send_tg("⚠️ Укажите название: /loadpreset NAME", chat_id, cfg)
            return
        presets = state["users"][str(chat_id)].get("presets", {})
        if name not in presets:
            await send_tg(f"❌ Пресет `{name}` не найден. /mypresets — список пресетов.", chat_id, cfg)
            return
        loaded = presets[name]
        state["users"][str(chat_id)]["settings"] = loaded.copy()
        save_state(state, cfg)
        s = loaded
        dest = s.get("destination", "")
        trip_type = trip_type_label(dest)
        text = (
            f"✅ Пресет `{name}` загружен:\n\n"
            f"✈️ Вылет: `{s['origin']}`\n"
            f"🔄 Тип: {trip_type}\n"
            f"📅 Дней вперёд: {s['days_ahead']}\n"
            f"💰 Базовая цена: {s['base_price_eur']}€\n"
            f"⏱️ Макс. длина: {s['base_duration_minutes']}мин\n"
            f"📈 Доп. €/30мин: {s['price_increment_eur']}€\n"
            f"🎯 Только прямые: {'✅' if s.get('direct_only') else '❌'}\n"
        )
        await send_tg(text, chat_id, cfg)
        return

    if cmd == "/deletepreset":
        name = arg.strip()
        if not name:
            await send_tg("⚠️ Укажите название: /deletepreset NAME", chat_id, cfg)
            return
        presets = state["users"][str(chat_id)].get("presets", {})
        if name not in presets:
            await send_tg(f"❌ Пресет `{name}` не найден. /mypresets — список пресетов.", chat_id, cfg)
            return
        del presets[name]
        save_state(state, cfg)
        await send_tg(f"🗑 Пресет `{name}` удалён.", chat_id, cfg)
        return

    if cmd == "/mypresets":
        presets = state["users"][str(chat_id)].get("presets", {})
        if not presets:
            await send_tg("У вас нет сохранённых пресетов.\n\n/savepreset NAME — сохранить текущие настройки.", chat_id, cfg)
            return
        lines = [f"📋 *Ваши пресеты* ({len(presets)}/{MAX_PRESETS}):\n"]
        for name, s in presets.items():
            dest = s.get("destination", "")
            trip = f"↔ {dest}" if dest else "→"
            lines.append(f"• `{name}` — {s['origin']} {trip}, {s['base_price_eur']}€ base, {s['days_ahead']}d")
        lines.append("\n/loadpreset NAME — загрузить\n/deletepreset NAME — удалить")
        await send_tg("\n".join(lines), chat_id, cfg)
        return

    # Unknown command
    logger.debug(f"❓ Unknown command received: {cmd}")
    await send_tg(
        "❓ Неизвестная команда. /help для справки.",
        chat_id, cfg
    )
    logger.debug(f"✅ Unknown command message sent to {chat_id}")


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def analytics_token(cfg: dict) -> str:
    """Derive analytics access token from existing secrets — no new config needed."""
    secret = f"{cfg.get('admin_chat_id', '')}:{cfg.get('telegram_bot_token', '')}"
    return hashlib.sha256(secret.encode()).hexdigest()[:32]


async def handle_analytics_data(request):
    """JSON endpoint: return aggregated analytics data."""
    cfg = request.app["cfg"]
    state = request.app["state"]

    token = request.rel_url.query.get("token", "")
    if token != analytics_token(cfg):
        return web.Response(status=403, text="Forbidden")

    daily = state.get("analytics", {}).get("daily", {})

    # Aggregate settings distributions across all users
    price_dist: dict = {}
    days_dist: dict = {}
    origin_totals: dict = {}
    dest_totals: dict = {}

    for uid, udata in state.get("users", {}).items():
        s = {**DEFAULT_USER_SETTINGS, **udata.get("settings", {})}
        p = str(s["base_price_eur"])
        price_dist[p] = price_dist.get(p, 0) + 1
        d = str(s["days_ahead"])
        days_dist[d] = days_dist.get(d, 0) + 1

    # Aggregate origins and destinations from daily stats
    for day_data in daily.values():
        for o, cnt in day_data.get("origins", {}).items():
            origin_totals[o] = origin_totals.get(o, 0) + cnt
        for d, cnt in day_data.get("destinations", {}).items():
            dest_totals[d] = dest_totals.get(d, 0) + cnt

    # User join history
    join_history: dict = {}
    for uid, udata in state.get("users", {}).items():
        jd = udata.get("joined_at", "")
        if jd and jd != "before-analytics":
            join_history[jd] = join_history.get(jd, 0) + 1

    # Also include daily join counters from analytics bucket
    for date, day_data in daily.items():
        joins = day_data.get("joins", 0)
        if joins:
            join_history[date] = join_history.get(date, 0) + joins

    data = {
        "total_users": len(state.get("users", {})),
        "total_deals_sent": sum(len(u.get("sent_deals", [])) for u in state.get("users", {}).values()),
        "users_before_analytics": sum(1 for u in state.get("users", {}).values() if u.get("joined_at") == "before-analytics"),
        "daily": daily,
        "join_history": dict(sorted(join_history.items())),
        "settings_price_dist": dict(sorted(price_dist.items(), key=lambda x: int(x[0]))),
        "settings_days_dist": dict(sorted(days_dist.items(), key=lambda x: int(x[0]))),
        "all_time_origins": dict(sorted(origin_totals.items(), key=lambda x: -x[1])),
        "all_time_destinations": dict(sorted(dest_totals.items(), key=lambda x: -x[1])),
    }
    return web.json_response(data)


ANALYTICS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Flight Deals Analytics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f1117; color: #e2e8f0; }
  .header { padding: 24px 32px; border-bottom: 1px solid #1e293b; }
  .header h1 { font-size: 20px; font-weight: 600; color: #f1f5f9; }
  .header p { font-size: 13px; color: #64748b; margin-top: 4px; }
  .kpi-row { display: flex; gap: 16px; padding: 24px 32px; flex-wrap: wrap; }
  .kpi { background: #1e293b; border-radius: 10px; padding: 20px 24px; flex: 1; min-width: 160px; }
  .kpi .label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; }
  .kpi .value { font-size: 32px; font-weight: 700; color: #f1f5f9; margin-top: 6px; }
  .kpi .sub { font-size: 12px; color: #94a3b8; margin-top: 4px; }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 0 32px 32px; }
  .chart-card { background: #1e293b; border-radius: 10px; padding: 20px; }
  .chart-card h2 { font-size: 13px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 16px; }
  .chart-card.wide { grid-column: span 2; }
  canvas { max-height: 220px; }
  .error { color: #f87171; padding: 32px; text-align: center; }
  @media (max-width: 768px) { .charts { grid-template-columns: 1fr; } .chart-card.wide { grid-column: span 1; } .kpi-row { flex-direction: column; } }
</style>
</head>
<body>
<div class="header">
  <h1>✈️ Flight Deals Analytics</h1>
  <p id="subtitle">Loading…</p>
</div>
<div class="kpi-row" id="kpis"></div>
<div class="charts" id="charts"></div>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
const BASE = location.origin;

function color(i, a=0.8) {
  const palette = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];
  return palette[i % palette.length].replace(')', `,${a})`).replace('#', 'rgba(').replace(/[0-9a-f]{2}/gi, h => parseInt(h,16)+',').slice(0,-1) + ')';
}
function hexToRgba(hex, a) {
  const r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${a})`;
}
const COLORS = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#f97316','#ec4899'];

async function load() {
  try {
    const res = await fetch(`${BASE}/flights/analytics/data?token=${token}`);
    if (!res.ok) { document.getElementById('charts').innerHTML = '<p class="error">Access denied. Check your token.</p>'; return; }
    const d = await res.json();
    render(d);
  } catch(e) {
    document.getElementById('charts').innerHTML = `<p class="error">Error: ${e.message}</p>`;
  }
}

function render(d) {
  document.getElementById('subtitle').textContent = `Last updated: ${new Date().toLocaleString()}`;

  const todayKey = new Date().toISOString().split('T')[0];
  const today = d.daily[todayKey] || {};

  document.getElementById('kpis').innerHTML = `
    <div class="kpi"><div class="label">Total Users</div><div class="value">${d.total_users}</div><div class="sub">${d.users_before_analytics} before analytics</div></div>
    <div class="kpi"><div class="label">Deals Sent (all time)</div><div class="value">${d.total_deals_sent}</div></div>
    <div class="kpi"><div class="label">Deals Sent Today</div><div class="value">${today.deals_sent || 0}</div></div>
    <div class="kpi"><div class="label">New Users Today</div><div class="value">${today.joins || 0}</div></div>
  `;

  const charts = document.getElementById('charts');
  charts.innerHTML = '';

  // Deals sent per day (last 30)
  const days30 = Object.entries(d.daily).slice(-30);
  if (days30.length) addChart(charts, 'Deals Sent Per Day (last 30)', 'bar', days30.map(x=>x[0]), days30.map(x=>x[1].deals_sent||0), 'wide');

  // Joins per day
  const joinDays = Object.entries(d.join_history).slice(-30);
  if (joinDays.length) addChart(charts, 'New Users Per Day (last 30)', 'line', joinDays.map(x=>x[0]), joinDays.map(x=>x[1]), '');

  // Top origins
  const origins = Object.entries(d.all_time_origins).slice(0,10);
  if (origins.length) addChart(charts, 'Top Origin Cities (all time)', 'bar', origins.map(x=>x[0]), origins.map(x=>x[1]), '');

  // Top destinations
  const dests = Object.entries(d.all_time_destinations).slice(0,10);
  if (dests.length) addChart(charts, 'Top Destinations (all time)', 'bar', dests.map(x=>x[0]), dests.map(x=>x[1]), '');

  // Price distribution
  const prices = Object.entries(d.settings_price_dist);
  if (prices.length) addChart(charts, 'Base Price Setting Distribution', 'bar', prices.map(x=>x[0]+'€'), prices.map(x=>x[1]), '');

  // Days ahead distribution
  const daysD = Object.entries(d.settings_days_dist);
  if (daysD.length) addChart(charts, 'Days Ahead Setting Distribution', 'bar', daysD.map(x=>x[0]+' days'), daysD.map(x=>x[1]), '');
}

function addChart(container, title, type, labels, data, extra) {
  const card = document.createElement('div');
  card.className = 'chart-card' + (extra === 'wide' ? ' wide' : '');
  card.innerHTML = `<h2>${title}</h2><canvas></canvas>`;
  container.appendChild(card);
  const ctx = card.querySelector('canvas').getContext('2d');
  const color0 = COLORS[0];
  new Chart(ctx, {
    type,
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: type === 'line' ? hexToRgba(color0, 0.15) : labels.map((_,i)=>hexToRgba(COLORS[i%COLORS.length],0.7)),
        borderColor: type === 'line' ? hexToRgba(color0, 0.9) : labels.map((_,i)=>hexToRgba(COLORS[i%COLORS.length],1)),
        borderWidth: type === 'line' ? 2 : 1,
        fill: type === 'line',
        tension: 0.3,
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 11 } }, grid: { color: '#1e293b' } },
        y: { ticks: { color: '#64748b', font: { size: 11 } }, grid: { color: '#334155' }, beginAtZero: true }
      }
    }
  });
}

load();
</script>
</body>
</html>"""


async def handle_analytics_page(request):
    """Serve the analytics HTML page."""
    cfg = request.app["cfg"]
    token = request.rel_url.query.get("token", "")
    if token != analytics_token(cfg):
        return web.Response(status=403, text="Forbidden")
    return web.Response(text=ANALYTICS_HTML, content_type="text/html")


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

    # Migration: backfill joined_at and analytics for existing users
    state.setdefault("analytics", {"daily": {}})
    for uid, udata in state.get("users", {}).items():
        if "joined_at" not in udata:
            udata["joined_at"] = "before-analytics"

    # Create HTTP session
    session = ClientSession()
    logger.debug("📡 HTTP session created")

    # Log analytics URL
    token = analytics_token(cfg)
    analytics_url = f"{cfg.get('webhook_host', '')}/flights/analytics?token={token}"
    logger.info(f"📊 Analytics: {analytics_url}")
    print(f"📊 Analytics URL: {analytics_url}", flush=True)

    # Set webhook
    logger.debug("🔌 Setting webhook...")
    await set_webhook(cfg)

    # Create web app
    app = web.Application()
    app["state"] = state
    app["cfg"] = cfg
    app.router.add_post(cfg["webhook_path"], handle_webhook)
    app.router.add_get("/flights/analytics", handle_analytics_page)
    app.router.add_get("/flights/analytics/data", handle_analytics_data)

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
