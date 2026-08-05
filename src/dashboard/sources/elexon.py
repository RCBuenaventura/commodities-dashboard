"""UK wholesale power from Elexon Insights — the market index price.

Elexon publishes a price per half-hour settlement period. The dashboard shows the
day's baseload average, which is only meaningful for a day that is substantially
complete: today's partial day is skipped rather than averaged, because a mean over
the fourteen periods so far is not "today's wholesale price".

``symbol`` is the data provider to read, e.g. ``APXMIDP``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import httpx

from dashboard.models import Quote
from dashboard.sources.base import FetchError, build_client, daily_average, get_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dashboard.config import Instrument

log = logging.getLogger(__name__)

MARKET_INDEX_URL: Final = (
    "https://data.elexon.co.uk/bmrs/api/v1/balancing/pricing/market-index"
)
UK: Final = ZoneInfo("Europe/London")

#: Enough to catch up after a failed run without asking for a large window.
LOOKBACK_DAYS: Final = 3


class ElexonSource:
    """Daily baseload averages of the half-hourly market index price."""

    id = "elexon"

    def __init__(
        self, client: httpx.Client | None = None, today: date | None = None
    ) -> None:
        self._client = client
        self._today = today

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        if not instruments:
            return []

        today = self._today or datetime.now(tz=UK).date()
        start = today - timedelta(days=LOOKBACK_DAYS)

        client = self._client or build_client()
        try:
            payload = get_json(
                client,
                MARKET_INDEX_URL,
                params={"from": start.isoformat(), "to": today.isoformat()},
                context="elexon market index",
            )
        finally:
            if self._client is None:
                client.close()

        quotes: list[Quote] = []
        for instrument in instruments:
            quotes.extend(parse_market_index(payload, instrument, today=today))
        if not quotes:
            raise FetchError("no complete settlement day in the response")
        return quotes


def parse_market_index(
    payload: object, instrument: Instrument, *, today: date
) -> list[Quote]:
    """Average each complete settlement day for one data provider."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise FetchError("elexon: no 'data' in response")

    by_day: defaultdict[date, list[float]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("dataProvider", "")) != instrument.symbol:
            continue
        day = _parse_date(row.get("settlementDate"))
        price = row.get("price")
        if day is None or isinstance(price, bool) or not isinstance(price, int | float):
            continue
        # A day that has not finished cannot be averaged into a daily price.
        if day > today:
            continue
        by_day[day].append(float(price))

    quotes: list[Quote] = []
    for day, prices in sorted(by_day.items()):
        average = daily_average(prices, label=f"elexon {instrument.id} {day}")
        if average is None:
            continue
        quotes.append(
            Quote(
                instrument=instrument.id,
                observed=day,
                value=average,
                unit=instrument.unit,
            )
        )
    return quotes


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
