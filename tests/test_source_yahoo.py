"""Yahoo chart parsing: bar dating, null closes, and the currency guard.

`yahoo_brent.json` is a real month of `BZ=F` daily bars, so the assertions here are
against what Yahoo actually sends. The edge cases that month did not contain — a
holiday, a currency mismatch — come from the `synthetic_` fixtures.
"""

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


def test_parses_one_quote_per_bar() -> None:
    payload = fixture_json("yahoo_brent.json")
    quotes = parse_chart(payload, instrument())

    assert len(quotes) == len(payload["chart"]["result"][0]["timestamp"])
    assert all(q.instrument == "brent" for q in quotes)
    assert all(q.unit == "USD/bbl" for q in quotes)


def test_quotes_come_back_in_date_order() -> None:
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())
    assert [q.observed for q in quotes] == sorted(q.observed for q in quotes)


def test_every_bar_gets_a_distinct_trading_date() -> None:
    """Dates are read through the exchange offset, so no two bars collapse together."""
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())
    assert len({q.observed for q in quotes}) == len(quotes)


def test_bars_land_on_weekdays() -> None:
    """Futures do not settle at weekends; an offset bug would put bars there."""
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())
    assert all(q.observed.weekday() < 5 for q in quotes)
    assert quotes[-1].observed == date(2026, 8, 5)


def test_prices_are_plausible_for_brent() -> None:
    """A scale or unit error would show up here long before it reached the page."""
    quotes = parse_chart(fixture_json("yahoo_brent.json"), instrument())
    assert all(20.0 < q.value < 300.0 for q in quotes)


def test_null_closes_are_skipped_not_filled() -> None:
    """A holiday has no close. It must leave a gap, not a carried-forward value."""
    payload = fixture_json("synthetic_yahoo_null_closes.json")
    closes = payload["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    assert closes.count(None) == 1

    quotes = parse_chart(payload, instrument())
    assert len(quotes) == len(closes) - 1


def test_delisted_symbol_raises() -> None:
    with pytest.raises(FetchError, match="Not Found"):
        parse_chart(fixture_json("yahoo_unknown_symbol.json"), instrument())


def test_currency_mismatch_is_refused() -> None:
    """A price in pence rendered under a USD label would be a wrong price."""
    with pytest.raises(FetchError, match="quoted in GBp"):
        parse_chart(fixture_json("synthetic_yahoo_gbp_mismatch.json"), instrument())


def test_currency_check_is_skipped_when_unconfigured() -> None:
    quotes = parse_chart(
        fixture_json("synthetic_yahoo_gbp_mismatch.json"), instrument(currency=None)
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
    assert len(source.fetch([instrument()])) > 10


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
