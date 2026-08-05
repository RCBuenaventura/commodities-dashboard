"""Carbon intensity: actuals only, never the forecast sitting next to them."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import error_client, fixture_json, instrument, json_client
from dashboard.sources.base import FetchError
from dashboard.sources.carbon import CarbonSource, parse_intensity

CARBON = {
    "id": "grid_carbon",
    "source": "carbon",
    "symbol": "national",
    "unit": "gCO2/kWh",
    "currency": None,
    "precision": 0,
}
DAY = date(2026, 8, 4)


def test_averages_the_settled_periods() -> None:
    quote = parse_intensity(
        fixture_json("carbon_intensity.json"), instrument(**CARBON), day=DAY
    )
    assert quote is not None
    assert quote.observed == DAY
    assert quote.unit == "gCO2/kWh"


def test_forecasts_are_never_substituted_for_missing_actuals() -> None:
    """Two periods have `actual: null` and a forecast beside them.

    The forecast is five higher than every actual in the fixture, so if it were
    being used to fill gaps the mean would land above the actual-only mean.
    """
    payload = fixture_json("carbon_intensity.json")
    quote = parse_intensity(payload, instrument(**CARBON), day=DAY)
    assert quote is not None

    actuals = [
        row["intensity"]["actual"]
        for row in payload["data"]
        if row["intensity"]["actual"] is not None
    ]
    assert quote.value == pytest.approx(sum(actuals) / len(actuals))


def test_a_day_too_sparse_to_average_returns_nothing() -> None:
    quote = parse_intensity(
        fixture_json("carbon_intensity_partial.json"), instrument(**CARBON), day=DAY
    )
    assert quote is None


def test_malformed_payload_raises() -> None:
    with pytest.raises(FetchError, match="no 'data'"):
        parse_intensity({}, instrument(**CARBON), day=DAY)


def test_fetch_end_to_end() -> None:
    source = CarbonSource(
        client=json_client(fixture_json("carbon_intensity.json")),
        today=date(2026, 8, 5),
    )
    quotes = source.fetch([instrument(**CARBON)])
    # The same fixture answers for every day in the window, so the source emits one
    # quote per day it asked for; what matters is that each is dated by its own day.
    assert len({q.observed for q in quotes}) == len(quotes)


def test_all_days_failing_raises() -> None:
    source = CarbonSource(client=error_client(503), today=date(2026, 8, 5))
    with pytest.raises(FetchError, match="no complete day"):
        source.fetch([instrument(**CARBON)])


def test_no_instruments_is_not_a_failure() -> None:
    assert CarbonSource(client=error_client(503)).fetch([]) == []
