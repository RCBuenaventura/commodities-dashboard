"""Yahoo chart parsing: bar dating, null closes, and the currency guard."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from conftest import (
    error_client,
    fixture_json,
    instrument,
    json_client,
    mock_client,
)
from dashboard.sources.base import FetchError
from dashboard.sources.yahoo import YahooSource, parse_chart


def test_parses_one_quote_per_trading_day() -> None:
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())

    # Five bars, one of which is a holiday with a null close.
    assert [q.observed for q in quotes] == [
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 7),
        date(2026, 8, 10),
    ]
    assert quotes[-1].value == 81.02
    assert quotes[0].unit == "USD/bbl"
    assert quotes[0].instrument == "brent"


def test_bars_are_dated_in_the_exchange_timezone() -> None:
    """A NYMEX bar opens at 13:30 UTC; read as UTC that is still the same day.

    The guard matters for the general case, so this pins the offset arithmetic:
    the fixture stamps 09:30 America/New_York with gmtoffset -14400.
    """
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())
    assert quotes[0].observed == date(2026, 8, 4)


def test_null_closes_are_skipped_not_filled() -> None:
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())
    assert date(2026, 8, 6) not in {q.observed for q in quotes}


def test_delisted_symbol_raises() -> None:
    with pytest.raises(FetchError, match="Not Found"):
        parse_chart(fixture_json("yahoo_unknown_symbol.json"), instrument())


def test_currency_mismatch_is_refused() -> None:
    """A price in pence rendered under a USD label would be a wrong price."""
    with pytest.raises(FetchError, match="quoted in GBp"):
        parse_chart(fixture_json("yahoo_gbp_mismatch.json"), instrument())


def test_currency_check_is_skipped_when_unconfigured() -> None:
    quotes = parse_chart(
        fixture_json("yahoo_gbp_mismatch.json"), instrument(currency=None)
    )
    assert len(quotes) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"chart": {}},
        {"chart": {"result": []}},
        {"chart": {"result": [{"meta": {}}]}},
        {"chart": {"result": [{"timestamp": [1], "indicators": {}}]}},
        {"chart": {"result": [{"timestamp": [1], "indicators": {"quote": [{}]}}]}},
    ],
)
def test_malformed_payloads_raise(payload: object) -> None:
    with pytest.raises(FetchError):
        parse_chart(payload, instrument())


def test_all_null_closes_raises() -> None:
    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "gmtoffset": 0},
                    "timestamp": [1754308200, 1754394600],
                    "indicators": {"quote": [{"close": [None, None]}]},
                }
            ],
            "error": None,
        }
    }
    with pytest.raises(FetchError, match="every close"):
        parse_chart(payload, instrument())


def test_fetch_uses_the_injected_client() -> None:
    source = YahooSource(client=json_client(fixture_json("yahoo_brent.json")))
    quotes = source.fetch([instrument()])
    assert len(quotes) == 4


def test_one_bad_symbol_does_not_sink_the_others() -> None:
    """A dead ticker must not cost the working ones their update."""
    good = fixture_json("yahoo_brent.json")
    bad = fixture_json("yahoo_unknown_symbol.json")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = bad if "COAL" in str(request.url) else good
        return httpx.Response(200, json=payload)

    source = YahooSource(client=mock_client(handler))
    quotes = source.fetch([instrument(), instrument(id="coal", symbol="COAL=F")])

    assert {q.instrument for q in quotes} == {"brent"}


def test_source_fails_only_when_nothing_came_back() -> None:
    source = YahooSource(client=error_client(503))
    with pytest.raises(FetchError, match="no symbol returned data"):
        source.fetch([instrument()])


def test_no_instruments_is_not_a_failure() -> None:
    assert YahooSource(client=error_client(503)).fetch([]) == []
