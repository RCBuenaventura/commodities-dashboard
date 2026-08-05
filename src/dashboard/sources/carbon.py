"""UK grid carbon intensity from National Grid ESO.

Only the **actual** intensity is stored. The API also returns a forecast for each
half-hour, and using it to fill gaps would be exactly the kind of plausible
substitution this project refuses: a forecast is not an observation. Periods with no
actual are skipped, and a day too sparse to average is skipped whole.
"""

from __future__ import annotations

import logging
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

DATE_URL: Final = "https://api.carbonintensity.org.uk/intensity/date/{day}"
UK: Final = ZoneInfo("Europe/London")

LOOKBACK_DAYS: Final = 2


class CarbonSource:
    """Daily mean of the half-hourly actual carbon intensity."""

    id = "carbon"

    def __init__(
        self, client: httpx.Client | None = None, today: date | None = None
    ) -> None:
        self._client = client
        self._today = today

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        if not instruments:
            return []

        today = self._today or datetime.now(tz=UK).date()
        days = [today - timedelta(days=n) for n in range(LOOKBACK_DAYS, -1, -1)]

        quotes: list[Quote] = []
        client = self._client or build_client()
        try:
            for day in days:
                try:
                    payload = get_json(
                        client,
                        DATE_URL.format(day=day.isoformat()),
                        context=f"carbon intensity {day}",
                    )
                except FetchError as exc:
                    log.warning("carbon: skipping %s: %s", day, exc)
                    continue
                for instrument in instruments:
                    quote = parse_intensity(payload, instrument, day=day)
                    if quote is not None:
                        quotes.append(quote)
        finally:
            if self._client is None:
                client.close()

        if not quotes:
            raise FetchError("no complete day of actual intensity in the window")
        return quotes


def parse_intensity(
    payload: object, instrument: Instrument, *, day: date
) -> Quote | None:
    """Mean actual intensity for one day, or ``None`` if the day is too sparse."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise FetchError(f"carbon: no 'data' for {day}")

    actuals: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        intensity = row.get("intensity")
        if not isinstance(intensity, dict):
            continue
        actual = intensity.get("actual")
        # `actual` is null for periods that have not settled. The forecast next to
        # it is deliberately ignored.
        if actual is None or isinstance(actual, bool):
            continue
        if isinstance(actual, int | float):
            actuals.append(float(actual))

    average = daily_average(actuals, label=f"carbon {day}")
    if average is None:
        return None
    return Quote(
        instrument=instrument.id,
        observed=day,
        value=average,
        unit=instrument.unit,
    )
