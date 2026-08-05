"""Octopus Agile: London-day bucketing, negative rates, partial-day refusal.

The fixture is three real days of half-hourly Agile rates, and it happens to contain
genuinely negative periods — so the negative-rate guard is tested against real data
rather than an invented case.
"""

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


def test_averages_complete_london_days() -> None:
    """Periods run 23:00Z to 23:00Z in BST — one London day, not one UTC day."""
    quotes = parse_unit_rates(
        fixture_json("octopus_agile.json"), instrument(**AGILE), today=TODAY
    )
    assert [q.observed for q in quotes] == [date(2026, 8, 3), date(2026, 8, 4)]


def test_the_fixture_really_does_contain_negative_rates() -> None:
    """Guards the guard: if the fixture stopped having negatives, the next test
    would keep passing while proving nothing."""
    rates = [r["value_inc_vat"] for r in fixture_json("octopus_agile.json")["results"]]
    assert min(rates) < 0


def test_negative_rates_are_kept_in_the_average() -> None:
    """Agile goes negative when the grid is long. Those are real prices.

    Dropping them as though they were bad data would bias every day upward, so the
    day's mean is checked against the mean of every period in that London day.
    """
    payload = fixture_json("octopus_agile.json")
    quotes = parse_unit_rates(payload, instrument(**AGILE), today=TODAY)
    day_three = [
        r["value_inc_vat"]
        for r in payload["results"]
        if r["valid_from"].startswith(("2026-08-02T23", "2026-08-03T"))
        and r["valid_from"] < "2026-08-03T23"
    ]
    assert quotes[0].value == pytest.approx(sum(day_three) / len(day_three))


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
    assert len(quotes) == 2
    assert all(q.unit == "p/kWh" for q in quotes)


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
