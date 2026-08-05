"""U.S. EIA Open Data v2 — official spot prices, and LPG in particular.

The only source in the project that needs a credential. The key comes from the
environment (a GitHub Actions secret in CI, `.env` locally) and is never written to
a log line: error messages here name the host and the series, never the URL, because
the key travels as a query parameter.

An absent key is reported as a source failure, not silently skipped — a run that
quietly stopped updating propane would look identical to one where the price simply
had not moved.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import TYPE_CHECKING, Final

import httpx

from dashboard.models import Quote
from dashboard.sources.base import FetchError, build_client, get_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dashboard.config import Instrument

log = logging.getLogger(__name__)

API_ROOT: Final = "https://api.eia.gov/v2"
API_KEY_ENV: Final = "EIA_API_KEY"

#: Enough history to backfill a chart on the first run.
LENGTH: Final = 40


class EIASource:
    """Series from EIA's v2 API.

    ``symbol`` is ``<route>:<series id>``, for example
    ``petroleum/pri/spt/data:EER_EPLLPA_PF4_Y44MB_DPG``. Keeping the route in config
    means another EIA series is an `instruments.toml` edit, not a code change.
    """

    id = "eia"

    def __init__(
        self, api_key: str | None = None, client: httpx.Client | None = None
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        self._client = client

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        if not self._api_key:
            raise FetchError(
                f"{API_KEY_ENV} is not set; cannot query EIA. Set it as a GitHub "
                f"Actions secret, or in .env locally."
            )

        quotes: list[Quote] = []
        failures: list[str] = []

        client = self._client or build_client()
        try:
            for instrument in instruments:
                try:
                    quotes.extend(self._fetch_one(client, instrument))
                except FetchError as exc:
                    log.warning("eia: skipping %s: %s", instrument.id, exc)
                    failures.append(instrument.id)
        finally:
            if self._client is None:
                client.close()

        if instruments and not quotes:
            raise FetchError(f"no series returned data (tried {', '.join(failures)})")
        return quotes

    def _fetch_one(self, client: httpx.Client, instrument: Instrument) -> list[Quote]:
        route, series_id = _split_symbol(instrument)
        payload = get_json(
            client,
            f"{API_ROOT}/{route}/",
            params={
                "api_key": self._api_key or "",
                "frequency": "daily",
                "data[0]": "value",
                "facets[series][]": series_id,
                "sort[0][column]": "period",
                "sort[0][direction]": "desc",
                "length": LENGTH,
            },
            context=f"eia {series_id}",
        )
        return parse_series(payload, instrument)


def _split_symbol(instrument: Instrument) -> tuple[str, str]:
    route, _, series_id = instrument.symbol.partition(":")
    if not route or not series_id:
        raise FetchError(
            f"{instrument.id}: symbol must be '<route>:<series id>', "
            f"got {instrument.symbol!r}"
        )
    return route.strip("/"), series_id


def parse_series(payload: object, instrument: Instrument) -> list[Quote]:
    """Turn an EIA v2 response into one quote per period with a value."""
    if not isinstance(payload, dict):
        raise FetchError(f"{instrument.id}: response was not an object")
    if error := payload.get("error"):
        raise FetchError(f"{instrument.id}: {error}")

    response = payload.get("response")
    if not isinstance(response, dict):
        raise FetchError(f"{instrument.id}: no 'response' in payload")
    rows = response.get("data")
    if not isinstance(rows, list):
        raise FetchError(f"{instrument.id}: 'data' was not a list")

    quotes: list[Quote] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        observed = _parse_period(row.get("period"))
        value = _parse_value(row.get("value"))
        # A null value is a day the series has no observation for. Skipping it is
        # the whole point; there is nothing legitimate to put in its place.
        if observed is None or value is None:
            continue
        quotes.append(
            Quote(
                instrument=instrument.id,
                observed=observed,
                value=value,
                unit=instrument.unit,
            )
        )

    if not quotes:
        raise FetchError(f"{instrument.id}: no usable rows in response")
    return sorted(quotes, key=lambda q: q.observed)


def _parse_period(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_value(value: object) -> float | None:
    """EIA returns numbers, numeric strings, or null depending on the dataset."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
