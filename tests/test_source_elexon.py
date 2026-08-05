"""Elexon: daily baseload averaging, provider filtering, partial-day refusal.

The fixture is four real settlement days. Two are complete, one is 46/48 (the window
boundary clipped it), and the newest is three periods in.
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import error_client, fixture_json, instrument, json_client
from dashboard.sources.base import FetchError
from dashboard.sources.elexon import ElexonSource, parse_market_index

MID = {
    "id": "uk_wholesale",
    "source": "elexon",
    "symbol": "APXMIDP",
    "unit": "GBP/MWh",
    "currency": "GBP",
}
TODAY = date(2026, 8, 5)


def test_averages_every_substantially_complete_day() -> None:
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert [q.observed for q in quotes] == [
        date(2026, 8, 2),
        date(2026, 8, 3),
        date(2026, 8, 4),
    ]


def test_a_day_clipped_by_the_window_still_averages() -> None:
    """2026-08-02 has 46 of 48 periods — sparse, but not misleadingly so."""
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert quotes[0].observed == date(2026, 8, 2)


def test_a_partial_day_is_not_averaged() -> None:
    """Three periods in, the mean is not 'today's wholesale price'."""
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert date(2026, 8, 5) not in {q.observed for q in quotes}


def test_prices_are_plausible_for_uk_wholesale() -> None:
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert all(20.0 < q.value < 500.0 for q in quotes)


def test_other_data_providers_are_ignored() -> None:
    """N2EXMIDP reports 0.00 throughout this window.

    That makes the filter load-bearing rather than cosmetic: averaging both
    providers together would halve the price and still look like a number.
    """
    apx = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    n2ex = parse_market_index(
        fixture_json("elexon_market_index.json"),
        instrument(**{**MID, "symbol": "N2EXMIDP"}),
        today=TODAY,
    )
    assert all(q.value == 0.0 for q in n2ex)
    assert all(q.value > 100.0 for q in apx)


def test_future_days_are_ignored() -> None:
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"),
        instrument(**MID),
        today=date(2026, 8, 1),
    )
    assert quotes == []


def test_malformed_payload_raises() -> None:
    with pytest.raises(FetchError, match="no 'data'"):
        parse_market_index({}, instrument(**MID), today=TODAY)


def test_fetch_end_to_end() -> None:
    source = ElexonSource(
        client=json_client(fixture_json("elexon_market_index.json")), today=TODAY
    )
    quotes = source.fetch([instrument(**MID)])
    assert len(quotes) == 3
    assert all(q.unit == "GBP/MWh" for q in quotes)


def test_http_failure_raises() -> None:
    with pytest.raises(FetchError):
        ElexonSource(client=error_client(500), today=TODAY).fetch([instrument(**MID)])


def test_no_complete_day_raises() -> None:
    source = ElexonSource(client=json_client({"data": []}), today=TODAY)
    with pytest.raises(FetchError, match="no complete settlement day"):
        source.fetch([instrument(**MID)])
