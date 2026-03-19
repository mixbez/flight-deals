"""
Pipeline tests for search_for_user() — the full deal-finding and delivery flow.

These tests mock only the two external boundaries (Aviasales API and Telegram),
letting the real orchestration logic run: filtering, deduplication, formatting,
and sent_deals updating.

Conservative by design — only asserts behaviors that have been true since v1.1:
  - cheap ticket found → message sent
  - expensive ticket → nothing sent
  - already-seen ticket → not sent again
  - sent_deals updated after delivery
  - direct_only setting respected

No real tokens or network required. Run with:
    pytest tests/test_pipeline.py -v
"""

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import search_for_user, DEFAULT_USER_SETTINGS, deal_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_state(settings_override=None, sent_deals=None):
    settings = {**DEFAULT_USER_SETTINGS, **(settings_override or {})}
    return {
        "users": {
            "999": {
                "name": "Test User",
                "settings": settings,
                "sent_deals": sent_deals or [],
                "joined_at": "2026-03-19",
            }
        },
        "pending": {},
        "revoked": {},
        "last_update_id": 0,
        "approval_required": True,
        "analytics": {"daily": {}},
    }


def make_cfg():
    return {
        "telegram_bot_token": "fake_token",
        "admin_chat_id": "999",
        "aviasales_token": "fake_aviasales",
        "webhook_host": "https://example.com",
        "webhook_port": 443,
        "listen_port": 8080,
        "webhook_path": "/webhook-flightdeals",
    }


# Departure date = tomorrow, always within the default days_ahead=3 window.
_TOMORROW = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT10:00:00")

CHEAP_TICKET = {
    "origin": "BUD",
    "destination": "LHR",
    "departure_at": _TOMORROW,
    "price": 15,
    "duration": 120,
    "transfers": 0,
    "airline": "W6",
    "flight_number": "W6 1234",
    "search_url": "https://aviasales.com/search/BUD-LHR",
}

EXPENSIVE_TICKET = {**CHEAP_TICKET, "price": 999}
CONNECTING_TICKET = {**CHEAP_TICKET, "transfers": 2}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cheap_ticket_triggers_send():
    """Core pipeline: cheap ticket within limits → Telegram message sent."""
    state = make_state()
    cfg = make_cfg()
    sent = []

    async def fake_fetch(*a, **kw): return [CHEAP_TICKET]
    async def fake_send(text, chat_id, cfg_, **kw): sent.append(text); return True

    with patch("main.fetch_flights", side_effect=fake_fetch), \
         patch("main.send_tg", side_effect=fake_send):
        await search_for_user("999", state, cfg)

    assert len(sent) == 1
    assert "BUD" in sent[0]
    assert "LHR" in sent[0]


@pytest.mark.asyncio
async def test_expensive_ticket_sends_nothing():
    """Ticket above price threshold → nothing sent."""
    state = make_state()
    cfg = make_cfg()
    sent = []

    async def fake_fetch(*a, **kw): return [EXPENSIVE_TICKET]
    async def fake_send(text, chat_id, cfg_, **kw): sent.append(text); return True

    with patch("main.fetch_flights", side_effect=fake_fetch), \
         patch("main.send_tg", side_effect=fake_send):
        await search_for_user("999", state, cfg)

    assert sent == []


@pytest.mark.asyncio
async def test_already_seen_deal_not_sent_again():
    """Deduplication: deal already in sent_deals is silently skipped."""
    existing_hash = deal_hash(CHEAP_TICKET)
    state = make_state(sent_deals=[existing_hash])
    cfg = make_cfg()
    sent = []

    async def fake_fetch(*a, **kw): return [CHEAP_TICKET]
    async def fake_send(text, chat_id, cfg_, **kw): sent.append(text); return True

    with patch("main.fetch_flights", side_effect=fake_fetch), \
         patch("main.send_tg", side_effect=fake_send):
        await search_for_user("999", state, cfg)

    assert sent == []


@pytest.mark.asyncio
async def test_sent_deals_updated_after_delivery():
    """After a deal is sent, its hash is persisted in sent_deals."""
    state = make_state()
    cfg = make_cfg()

    async def fake_fetch(*a, **kw): return [CHEAP_TICKET]
    async def fake_send(*a, **kw): return True

    with patch("main.fetch_flights", side_effect=fake_fetch), \
         patch("main.send_tg", side_effect=fake_send):
        await search_for_user("999", state, cfg)

    assert deal_hash(CHEAP_TICKET) in state["users"]["999"]["sent_deals"]


@pytest.mark.asyncio
async def test_direct_only_blocks_connecting_flight():
    """direct_only=True: connecting ticket is filtered out, nothing sent."""
    state = make_state(settings_override={"direct_only": True})
    cfg = make_cfg()
    sent = []

    async def fake_fetch(*a, **kw): return [CONNECTING_TICKET]
    async def fake_send(text, chat_id, cfg_, **kw): sent.append(text); return True

    with patch("main.fetch_flights", side_effect=fake_fetch), \
         patch("main.send_tg", side_effect=fake_send):
        await search_for_user("999", state, cfg)

    assert sent == []


@pytest.mark.asyncio
async def test_empty_api_response_sends_nothing():
    """API returns empty list → no crash, nothing sent."""
    state = make_state()
    cfg = make_cfg()

    async def fake_fetch(*a, **kw): return []
    async def fake_send(*a, **kw): raise AssertionError("should not send")

    with patch("main.fetch_flights", side_effect=fake_fetch), \
         patch("main.send_tg", side_effect=fake_send):
        await search_for_user("999", state, cfg)  # must not raise
