"""Octopus Agile: London-day bucketing, negative rates, partial-day refusal."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import error_client, fixture_json, instrument, json_client
from dashboard.sources.base import FetchError
from dashboard.sources.octopus import OctopusSource, parse_unit_rates

AGILE = {
    "id": "uk_agile",
    "source": "octopus",
    "symbol": "AGILE-24-10-01/E-1R-AGILE-24-10-01-C",
    "unit": "p/kWh",
    "currency": "GBP",
}
TODAY = date(2026, 8, 5)


def test_averages_a_full_london_day() -> None:
    """The fixture's 48 periods run 23:00Z to 23:00Z — one BST day, not one UTC day."""
    quotes = parse_unit_rates(
        fixture_json("octopus_agile.json"), instrument(**AGILE), today=TODAY
    )
    assert [q.observed for q in quotes] == [date(2026, 8, 4)]


def test_negative_rates_are_included_in_the_average() -> None:
    """Agile goes negative; dropping those periods would overstate the day."""
    quotes = parse_unit_rates(
        fixture_json("octopus_agile.json"), instrument(**AGILE), today=TODAY
    )
    assert quotes[0].value < 16.0


def test_a_partial_day_is_skipped() -> None:
    quotes = parse_unit_rates(
        fixture_json("octopus_agile.json"), instrument(**AGILE), today=TODAY
    )
    assert date(2026, 8, 5) not in {q.observed for q in quotes}


def test_no_complete_day_raises() -> None:
    with pytest.raises(FetchError, match="no complete day"):
        parse_unit_rates({"results": []}, instrument(**AGILE), today=TODAY)


def test_malformed_payload_raises() -> None:
    with pytest.raises(FetchError, match="no 'results'"):
        parse_unit_rates({}, instrument(**AGILE), today=TODAY)


def test_fetch_end_to_end() -> None:
    source = OctopusSource(
        client=json_client(fixture_json("octopus_agile.json")), today=TODAY
    )
    quotes = source.fetch([instrument(**AGILE)])
    assert len(quotes) == 1
    assert quotes[0].unit == "p/kWh"


def test_symbol_must_carry_product_and_tariff() -> None:
    source = OctopusSource(
        client=json_client(fixture_json("octopus_agile.json")), today=TODAY
    )
    with pytest.raises(FetchError, match="no tariff returned data"):
        source.fetch([instrument(**{**AGILE, "symbol": "AGILE-24-10-01"})])


def test_http_failure_raises() -> None:
    source = OctopusSource(client=error_client(502), today=TODAY)
    with pytest.raises(FetchError):
        source.fetch([instrument(**AGILE)])
