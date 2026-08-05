"""Elexon: daily baseload averaging, provider filtering, partial-day refusal."""

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


def test_averages_a_complete_settlement_day() -> None:
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert [q.observed for q in quotes] == [date(2026, 8, 4)]
    assert 64.0 <= quotes[0].value <= 82.0


def test_a_partial_day_is_not_averaged() -> None:
    """Ten periods in, the mean is not 'today's wholesale price'."""
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert date(2026, 8, 5) not in {q.observed for q in quotes}


def test_other_data_providers_are_ignored() -> None:
    """The fixture carries N2EXMIDP rows at 999.99; picking them up would show."""
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"), instrument(**MID), today=TODAY
    )
    assert quotes[0].value < 100


def test_selecting_the_other_provider_works() -> None:
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"),
        instrument(**{**MID, "symbol": "N2EXMIDP"}),
        today=TODAY,
    )
    assert quotes[0].value == pytest.approx(999.99)


def test_future_days_are_ignored() -> None:
    quotes = parse_market_index(
        fixture_json("elexon_market_index.json"),
        instrument(**MID),
        today=date(2026, 8, 3),
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
    assert len(quotes) == 1
    assert quotes[0].unit == "GBP/MWh"


def test_http_failure_raises() -> None:
    with pytest.raises(FetchError):
        ElexonSource(client=error_client(500), today=TODAY).fetch([instrument(**MID)])


def test_no_complete_day_raises() -> None:
    source = ElexonSource(client=json_client({"data": []}), today=TODAY)
    with pytest.raises(FetchError, match="no complete settlement day"):
        source.fetch([instrument(**MID)])
