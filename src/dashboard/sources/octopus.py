"""UK retail power — the Octopus Agile half-hourly unit rate.

The dashboard shows the day's average inc-VAT unit rate. As with wholesale, a day is
only averaged once substantially complete, and days are bucketed by **London local
date** rather than UTC: the rate a household pays on the 5th is the rate during the
5th in London, and in summer those are not the same window.

Agile goes negative when the grid is long. That is a real price, and it is stored.

``symbol`` is ``<product code>/<tariff code>``.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import httpx

from dashboard.models import Quote
from dashboard.sources.base import FetchError, build_client, daily_average, get_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dashboard.config import Instrument

log = logging.getLogger(__name__)

RATES_URL: Final = (
    "https://api.octopus.energy/v1/products/{product}"
    "/electricity-tariffs/{tariff}/standard-unit-rates/"
)
UK: Final = ZoneInfo("Europe/London")

LOOKBACK_DAYS: Final = 3

#: Large enough that three days of half-hourly rates arrive in one page, so the
#: source never has to follow pagination.
PAGE_SIZE: Final = 1500


class OctopusSource:
    """Daily averages of the Agile unit rate."""

    id = "octopus"

    def __init__(
        self, client: httpx.Client | None = None, today: date | None = None
    ) -> None:
        self._client = client
        self._today = today

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        quotes: list[Quote] = []
        failures: list[str] = []
        today = self._today or datetime.now(tz=UK).date()

        client = self._client or build_client()
        try:
            for instrument in instruments:
                try:
                    quotes.extend(self._fetch_one(client, instrument, today))
                except FetchError as exc:
                    log.warning("octopus: skipping %s: %s", instrument.id, exc)
                    failures.append(instrument.id)
        finally:
            if self._client is None:
                client.close()

        if instruments and not quotes:
            raise FetchError(f"no tariff returned data (tried {', '.join(failures)})")
        return quotes

    def _fetch_one(
        self, client: httpx.Client, instrument: Instrument, today: date
    ) -> list[Quote]:
        product, _, tariff = instrument.symbol.partition("/")
        if not product or not tariff:
            raise FetchError(
                f"{instrument.id}: symbol must be '<product>/<tariff>', "
                f"got {instrument.symbol!r}"
            )

        start = datetime.combine(
            today - timedelta(days=LOOKBACK_DAYS), datetime.min.time(), tzinfo=UK
        )
        end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UK)
        payload = get_json(
            client,
            RATES_URL.format(product=product, tariff=tariff),
            params={
                "period_from": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "period_to": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "page_size": PAGE_SIZE,
            },
            context=f"octopus {instrument.symbol}",
        )
        return parse_unit_rates(payload, instrument, today=today)


def parse_unit_rates(
    payload: object, instrument: Instrument, *, today: date
) -> list[Quote]:
    """Average each complete London day of half-hourly unit rates."""
    if not isinstance(payload, dict):
        raise FetchError(f"{instrument.id}: response was not an object")
    results = payload.get("results")
    if not isinstance(results, list):
        raise FetchError(f"{instrument.id}: no 'results' in response")

    by_day: defaultdict[date, list[float]] = defaultdict(list)
    for row in results:
        if not isinstance(row, dict):
            continue
        starts = _parse_dt(row.get("valid_from"))
        value = row.get("value_inc_vat")
        if starts is None or isinstance(value, bool):
            continue
        if not isinstance(value, int | float):
            continue
        day = starts.astimezone(UK).date()
        if day > today:
            continue
        by_day[day].append(float(value))

    quotes: list[Quote] = []
    for day, values in sorted(by_day.items()):
        average = daily_average(values, label=f"octopus {instrument.id} {day}")
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

    if not quotes:
        raise FetchError(f"{instrument.id}: no complete day in the response")
    return quotes


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
