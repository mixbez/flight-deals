"""
Flight Deal Finder — searches cheap one-way flights via Aviasales (Travelpayouts) API
and sends Telegram notifications when deals are found.
"""

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
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Load configuration from JSON file (if exists), with env-var overrides."""
    cfg = dict(DEFAULTS)

    if path.exists():
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))

    # Environment variables take priority (useful for CI / GitHub Actions)
    cfg["aviasales_token"] = os.environ.get("AVIASALES_TOKEN", cfg.get("aviasales_token", ""))
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", cfg.get("telegram_bot_token", ""))
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", cfg.get("telegram_chat_id", ""))

    # Also allow overriding search parameters from env
    if os.environ.get("FLIGHT_ORIGIN"):
        cfg["origin"] = os.environ["FLIGHT_ORIGIN"]
    if os.environ.get("FLIGHT_DAYS_AHEAD"):
        cfg["days_ahead"] = int(os.environ["FLIGHT_DAYS_AHEAD"])

    return cfg


# ---------------------------------------------------------------------------
# Price threshold logic
# ---------------------------------------------------------------------------


def max_price_for_duration(duration_minutes: int, cfg: dict) -> float:
    """
    Calculate the maximum acceptable price for a given flight duration.

    Default logic:
        - Flights ≤ 90 min → €20
        - Each additional 30 min → +€10

    All parameters are configurable via config.json.
    """
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
    """
    Fetch one-way flights from the Aviasales cache for a given departure date.

    Returns a list of ticket dicts from the API.
    """
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
    """Return only tickets whose price is below the duration-based threshold."""
    deals = []
    for t in tickets:
        duration = t.get("duration_to") or t.get("duration", 0)
        if duration <= 0:
            # Skip tickets without duration info
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
# Telegram notification
# ---------------------------------------------------------------------------

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


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


def send_telegram(deals: list[dict], cfg: dict) -> None:
    """Send a Telegram message with all found deals."""
    bot_token = cfg["telegram_bot_token"]
    chat_id = cfg["telegram_chat_id"]

    if not bot_token or not chat_id:
        print("⚠️  Telegram not configured — skipping notification.")
        return

    header = f"🔥 Found {len(deals)} cheap flight(s)!\n\n"
    body = "\n\n".join(format_deal(d) for d in deals)
    text = header + body

    # Telegram limits message to 4096 chars
    if len(text) > 4096:
        text = text[:4090] + "\n…"

    resp = requests.post(
        TELEGRAM_API.format(token=bot_token),
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )
    if resp.ok:
        print(f"✅ Telegram message sent ({len(deals)} deal(s)).")
    else:
        print(f"❌ Telegram error: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    cfg = load_config()

    if not cfg.get("aviasales_token"):
        print("❌ aviasales_token is not set. Set it in config.json or AVIASALES_TOKEN env var.")
        sys.exit(1)

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
            print(f"   🎉 {len(deals)} deal(s) found!")
        all_deals.extend(deals)

    print(f"\n{'='*50}")
    print(f"Total deals: {len(all_deals)}")

    if all_deals:
        # Sort by price
        all_deals.sort(key=lambda d: d["price"])

        # Print to console
        for d in all_deals:
            print(format_deal(d))
            print()

        # Send to Telegram
        send_telegram(all_deals, cfg)
    else:
        print("No deals found this time.")


if __name__ == "__main__":
    main()
