"""Frankfurter FX: the observed date comes from the fixing, not from the clock."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import error_client, fixture_json, instrument, json_client
from dashboard.sources.base import FetchError
from dashboard.sources.fx import FXSource, parse_rates

USD = {"id": "gbpusd", "source": "fx", "symbol": "USD", "unit": "USD per GBP"}
EUR = {"id": "gbpeur", "source": "fx", "symbol": "EUR", "unit": "EUR per GBP"}


def test_one_response_covers_every_pair() -> None:
    quotes = parse_rates(
        fixture_json("frankfurter_latest.json"),
        [instrument(**USD), instrument(**EUR)],
    )
    assert {q.instrument: q.value for q in quotes} == {
        "gbpusd": 1.3479,
        "gbpeur": 1.1666,
    }


def test_the_observed_date_is_the_ecb_fixing_date() -> None:
    """On a weekend the rate is Friday's, and it is dated Friday."""
    quotes = parse_rates(fixture_json("frankfurter_latest.json"), [instrument(**USD)])
    assert quotes[0].observed == date(2026, 8, 5)


def test_a_missing_pair_is_skipped_not_invented() -> None:
    quotes = parse_rates(
        fixture_json("frankfurter_latest.json"),
        [instrument(**USD), instrument(id="gbpjpy", source="fx", symbol="JPY")],
    )
    assert {q.instrument for q in quotes} == {"gbpusd"}


def test_a_different_base_is_refused() -> None:
    """Rates per EUR rendered as rates per GBP would be silently wrong."""
    payload = {**fixture_json("frankfurter_latest.json"), "base": "EUR"}
    with pytest.raises(FetchError, match="per EUR"):
        parse_rates(payload, [instrument(**USD)])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"date": "2026-08-04"},
        {"date": "not-a-date", "rates": {"USD": 1.2}},
        {"date": "2026-08-04", "rates": {}},
    ],
)
def test_malformed_payloads_raise(payload: object) -> None:
    with pytest.raises(FetchError):
        parse_rates(payload, [instrument(**USD)])


def test_fetch_end_to_end() -> None:
    source = FXSource(client=json_client(fixture_json("frankfurter_latest.json")))
    assert len(source.fetch([instrument(**USD), instrument(**EUR)])) == 2


def test_http_failure_raises() -> None:
    with pytest.raises(FetchError):
        FXSource(client=error_client(500)).fetch([instrument(**USD)])


def test_no_instruments_is_not_a_failure() -> None:
    assert FXSource(client=error_client(500)).fetch([]) == []
