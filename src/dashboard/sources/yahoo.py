"""Yahoo Finance chart endpoint — futures and metals.

The endpoint is undocumented, unauthenticated and makes no uptime promise, which is
precisely why the orchestrator treats every source as skippable. It answers 403 to a
default client, so it needs the browser user-agent from :mod:`~dashboard.sources.base`.

Quotes are dated by the **bar's own trading date**, not by the day the fetcher ran. A
market that has not settled today simply yields yesterday's bar again, the store sees
no change, and the tile is badged stale — which is the truth. Nothing is carried
forward to make the page look current.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import httpx

from dashboard.models import Quote
from dashboard.sources.base import FetchError, build_client, get_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dashboard.config import Instrument

log = logging.getLogger(__name__)

CHART_URL: Final = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

#: A month of daily bars. More than the page needs day to day, but it means the very
#: first run backfills a usable chart instead of a single point.
RANGE: Final = "1mo"
INTERVAL: Final = "1d"


class YahooSource:
    """Daily closes for anything with a Yahoo ticker."""

    id = "yahoo"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        """Fetch every configured symbol, tolerating individual symbol failures.

        One dead ticker should not cost the other fifteen their update, so failures
        are collected per symbol. The source only declares itself down when nothing
        at all came back.
        """
        quotes: list[Quote] = []
        failures: list[str] = []

        client = self._client or build_client()
        try:
            for instrument in instruments:
                try:
                    quotes.extend(self._fetch_one(client, instrument))
                except FetchError as exc:
                    log.warning("yahoo: skipping %s: %s", instrument.id, exc)
                    failures.append(instrument.id)
        finally:
            if self._client is None:
                client.close()

        if instruments and not quotes:
            raise FetchError(f"no symbol returned data (tried {', '.join(failures)})")
        return quotes

    def _fetch_one(self, client: httpx.Client, instrument: Instrument) -> list[Quote]:
        payload = get_json(
            client,
            CHART_URL.format(symbol=instrument.symbol),
            params={"range": RANGE, "interval": INTERVAL},
            context=f"yahoo {instrument.symbol}",
        )
        return parse_chart(payload, instrument)


def parse_chart(payload: object, instrument: Instrument) -> list[Quote]:
    """Turn a chart response into one quote per trading day it contains."""
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict):
        raise FetchError(f"{instrument.symbol}: no 'chart' in response")
    if chart.get("error"):
        raise FetchError(f"{instrument.symbol}: {chart['error']}")

    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise FetchError(f"{instrument.symbol}: empty result")
    result = results[0]
    if not isinstance(result, dict):
        raise FetchError(f"{instrument.symbol}: malformed result")

    meta = result.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    _check_currency(meta, instrument)

    timestamps = result.get("timestamp")
    closes = _closes(result, instrument)
    if not isinstance(timestamps, list) or not timestamps:
        raise FetchError(f"{instrument.symbol}: no timestamps")

    # Daily bars are stamped at the exchange's local open, so the trading date has to
    # be read in the exchange's offset. Using UTC would misdate bars either side of
    # midnight for anything not quoted in London.
    offset = meta.get("gmtoffset")
    offset = int(offset) if isinstance(offset, int) else 0

    by_date: dict[Any, float] = {}
    for stamp, close in zip(timestamps, closes, strict=False):
        if close is None or not isinstance(stamp, int | float):
            continue
        if isinstance(close, bool) or not isinstance(close, int | float):
            continue
        observed = datetime.fromtimestamp(float(stamp) + offset, tz=UTC).date()
        by_date[observed] = float(close)

    if not by_date:
        raise FetchError(f"{instrument.symbol}: every close in the range was null")

    return [
        Quote(
            instrument=instrument.id,
            observed=observed,
            value=value,
            unit=instrument.unit,
        )
        for observed, value in sorted(by_date.items())
    ]


def _closes(result: dict[str, Any], instrument: Instrument) -> list[Any]:
    indicators = result.get("indicators")
    if not isinstance(indicators, dict):
        raise FetchError(f"{instrument.symbol}: no indicators")
    quote_blocks = indicators.get("quote")
    if not isinstance(quote_blocks, list) or not quote_blocks:
        raise FetchError(f"{instrument.symbol}: no quote block")
    block = quote_blocks[0]
    closes = block.get("close") if isinstance(block, dict) else None
    if not isinstance(closes, list):
        raise FetchError(f"{instrument.symbol}: no closes")
    return closes


def _check_currency(meta: dict[str, Any], instrument: Instrument) -> None:
    """Refuse a value quoted in a currency the tile does not claim.

    Yahoo occasionally reports a different currency than expected for a ticker
    (pence versus pounds, most often). Rendering that number under the configured
    unit would put a wrong price on the page, so it is a hard skip.
    """
    reported = meta.get("currency")
    if not instrument.currency or not isinstance(reported, str):
        return
    if reported.upper() != instrument.currency.upper():
        raise FetchError(
            f"{instrument.symbol}: quoted in {reported}, but configured as "
            f"{instrument.currency}"
        )
