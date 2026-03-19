"""
Unit tests for pure logic functions in main.py.
No network calls, no Telegram, no Aviasales API required.

Run with:
    pip install pytest
    pytest tests/test_logic.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import deal_hash, max_price_for_duration, filter_deals, format_deal


# ---------------------------------------------------------------------------
# Fixtures / shared test data
# ---------------------------------------------------------------------------

BASE_SETTINGS = {
    "base_price_eur": 20,
    "base_duration_minutes": 90,
    "price_increment_eur": 10,
    "increment_minutes": 30,
    "direct_only": False,
}

ONE_WAY_DEAL = {
    "origin": "BUD",
    "destination": "LON",
    "departure_at": "2026-04-01T06:30:00",
    "price": 15,
    "duration": 150,
    "transfers": 0,
    "airline": "W6",
    "flight_number": "W6 1234",
    "search_url": "https://aviasales.com/search",
}

ROUND_TRIP_DEAL = {
    "_outbound": {
        "origin": "BUD",
        "destination": "LON",
        "departure_at": "2026-04-01T06:30:00",
        "price": 15,
        "duration": 150,
        "transfers": 0,
        "airline": "W6",
        "flight_number": "W6 1234",
        "search_url": "https://aviasales.com/search",
    },
    "_return": {
        "origin": "LON",
        "destination": "BUD",
        "departure_at": "2026-04-08T14:00:00",
        "price": 18,
        "duration": 145,
        "transfers": 0,
        "airline": "W6",
        "flight_number": "W6 1235",
        "search_url": "https://aviasales.com/search",
    },
    "_total_price": 33,
}


# ---------------------------------------------------------------------------
# deal_hash
# ---------------------------------------------------------------------------

class TestDealHash:
    def test_same_one_way_deal_produces_same_hash(self):
        h1 = deal_hash(ONE_WAY_DEAL)
        h2 = deal_hash(ONE_WAY_DEAL.copy())
        assert h1 == h2

    def test_different_price_produces_different_hash(self):
        other = {**ONE_WAY_DEAL, "price": 99}
        assert deal_hash(ONE_WAY_DEAL) != deal_hash(other)

    def test_different_date_produces_different_hash(self):
        other = {**ONE_WAY_DEAL, "departure_at": "2026-05-01T06:30:00"}
        assert deal_hash(ONE_WAY_DEAL) != deal_hash(other)

    def test_different_route_produces_different_hash(self):
        other = {**ONE_WAY_DEAL, "destination": "VIE"}
        assert deal_hash(ONE_WAY_DEAL) != deal_hash(other)

    def test_returns_32_char_hex_string(self):
        h = deal_hash(ONE_WAY_DEAL)
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_round_trip_hash_uses_total_price(self):
        other = {**ROUND_TRIP_DEAL, "_total_price": 99}
        assert deal_hash(ROUND_TRIP_DEAL) != deal_hash(other)

    def test_round_trip_hash_stable(self):
        assert deal_hash(ROUND_TRIP_DEAL) == deal_hash(ROUND_TRIP_DEAL)

    def test_one_way_and_round_trip_differ(self):
        # A one-way deal should never accidentally collide with a round-trip
        assert deal_hash(ONE_WAY_DEAL) != deal_hash(ROUND_TRIP_DEAL)


# ---------------------------------------------------------------------------
# max_price_for_duration
# ---------------------------------------------------------------------------

class TestMaxPriceForDuration:
    def test_at_base_duration_returns_base_price(self):
        assert max_price_for_duration(90, BASE_SETTINGS) == 20

    def test_below_base_duration_returns_base_price(self):
        assert max_price_for_duration(60, BASE_SETTINGS) == 20
        assert max_price_for_duration(0, BASE_SETTINGS) == 20

    def test_one_block_over_adds_one_increment(self):
        # 91–120 minutes = 1 block of 30 min over base → base + 1*10 = 30
        assert max_price_for_duration(91, BASE_SETTINGS) == 30
        assert max_price_for_duration(120, BASE_SETTINGS) == 30

    def test_two_blocks_over_adds_two_increments(self):
        # 121–150 minutes = 2 blocks → base + 2*10 = 40
        assert max_price_for_duration(121, BASE_SETTINGS) == 40
        assert max_price_for_duration(150, BASE_SETTINGS) == 40

    def test_partial_block_rounds_up(self):
        # 91 minutes = 1 minute over → still counts as 1 full block
        assert max_price_for_duration(91, BASE_SETTINGS) == 30

    def test_custom_settings(self):
        settings = {
            "base_price_eur": 30,
            "base_duration_minutes": 60,
            "price_increment_eur": 5,
            "increment_minutes": 15,
        }
        # 75 min = 15 min over = 1 block → 30 + 5 = 35
        assert max_price_for_duration(75, settings) == 35


# ---------------------------------------------------------------------------
# filter_deals
# ---------------------------------------------------------------------------

class TestFilterDeals:
    def _make_ticket(self, price, duration, transfers=0):
        return {"price": price, "duration": duration, "transfers": transfers}

    def test_cheap_short_flight_passes(self):
        tickets = [self._make_ticket(price=10, duration=60)]
        result = filter_deals(tickets, BASE_SETTINGS)
        assert len(result) == 1

    def test_expensive_flight_filtered_out(self):
        # 90 min = base → max price 20. Price 21 should fail.
        tickets = [self._make_ticket(price=21, duration=90)]
        assert filter_deals(tickets, BASE_SETTINGS) == []

    def test_price_at_exact_limit_passes(self):
        tickets = [self._make_ticket(price=20, duration=90)]
        assert len(filter_deals(tickets, BASE_SETTINGS)) == 1

    def test_missing_price_filtered_out(self):
        tickets = [{"duration": 60, "transfers": 0}]
        assert filter_deals(tickets, BASE_SETTINGS) == []

    def test_direct_only_filters_connecting_flights(self):
        settings = {**BASE_SETTINGS, "direct_only": True}
        tickets = [
            self._make_ticket(price=10, duration=60, transfers=0),  # direct — pass
            self._make_ticket(price=10, duration=60, transfers=1),  # connecting — fail
        ]
        result = filter_deals(tickets, settings)
        assert len(result) == 1
        assert result[0]["transfers"] == 0

    def test_direct_only_false_allows_connecting(self):
        settings = {**BASE_SETTINGS, "direct_only": False}
        tickets = [self._make_ticket(price=10, duration=60, transfers=2)]
        assert len(filter_deals(tickets, settings)) == 1

    def test_empty_list_returns_empty(self):
        assert filter_deals([], BASE_SETTINGS) == []

    def test_longer_flight_allowed_at_higher_price(self):
        # 150 min = 2 blocks over 90 → max price 40
        tickets = [self._make_ticket(price=38, duration=150)]
        assert len(filter_deals(tickets, BASE_SETTINGS)) == 1

    def test_longer_flight_rejected_above_threshold(self):
        tickets = [self._make_ticket(price=41, duration=150)]
        assert filter_deals(tickets, BASE_SETTINGS) == []


# ---------------------------------------------------------------------------
# format_deal
# ---------------------------------------------------------------------------

class TestFormatDeal:
    def test_one_way_contains_route(self):
        msg = format_deal(ONE_WAY_DEAL, is_round_trip=False)
        assert "BUD" in msg
        assert "LON" in msg

    def test_one_way_contains_price(self):
        msg = format_deal(ONE_WAY_DEAL, is_round_trip=False)
        assert "15" in msg

    def test_one_way_contains_date(self):
        msg = format_deal(ONE_WAY_DEAL, is_round_trip=False)
        assert "2026-04-01" in msg

    def test_one_way_contains_duration(self):
        # 150 min = 2h30m
        msg = format_deal(ONE_WAY_DEAL, is_round_trip=False)
        assert "2h30m" in msg

    def test_one_way_direct_flag(self):
        msg = format_deal(ONE_WAY_DEAL, is_round_trip=False)
        assert "direct" in msg

    def test_one_way_connecting_flag(self):
        deal = {**ONE_WAY_DEAL, "transfers": 2}
        msg = format_deal(deal, is_round_trip=False)
        assert "1+ stops" in msg

    def test_one_way_contains_attribution(self):
        msg = format_deal(ONE_WAY_DEAL, is_round_trip=False)
        assert "aboutmisha.com" in msg

    def test_round_trip_contains_both_legs(self):
        msg = format_deal(ROUND_TRIP_DEAL, is_round_trip=True)
        assert "BUD" in msg
        assert "LON" in msg
        # Both departure dates present
        assert "2026-04-01" in msg
        assert "2026-04-08" in msg

    def test_round_trip_shows_total_price(self):
        msg = format_deal(ROUND_TRIP_DEAL, is_round_trip=True)
        assert "33" in msg

    def test_round_trip_contains_attribution(self):
        msg = format_deal(ROUND_TRIP_DEAL, is_round_trip=True)
        assert "aboutmisha.com" in msg

    def test_missing_optional_fields_dont_crash(self):
        minimal = {
            "origin": "BUD",
            "destination": "LON",
            "departure_at": "2026-04-01T06:30:00",
            "price": 10,
            "duration": 60,
        }
        msg = format_deal(minimal, is_round_trip=False)
        assert "BUD" in msg
        assert "LON" in msg
