"""GBP reference rates from Frankfurter, which republishes ECB daily fixings.

These exist so USD- and EUR-quoted instruments can be read in sterling. ECB fixings
are published once a working day, so the observed date comes from the response rather
than from the clock — on a weekend the rate is Friday's, dated Friday.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Final

import httpx

from dashboard.models import Quote
from dashboard.sources.base import FetchError, build_client, get_json

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dashboard.config import Instrument

log = logging.getLogger(__name__)

LATEST_URL: Final = "https://api.frankfurter.app/latest"

#: This is a sterling-referenced dashboard: every FX instrument is "x per GBP".
BASE: Final = "GBP"


class FXSource:
    """One request covers every configured pair; ``symbol`` is the quote currency."""

    id = "fx"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        if not instruments:
            return []

        symbols = ",".join(sorted({i.symbol.upper() for i in instruments}))
        client = self._client or build_client()
        try:
            payload = get_json(
                client,
                LATEST_URL,
                params={"base": BASE, "symbols": symbols},
                context="frankfurter",
            )
        finally:
            if self._client is None:
                client.close()

        return parse_rates(payload, instruments)


def parse_rates(payload: object, instruments: Sequence[Instrument]) -> list[Quote]:
    """Turn a Frankfurter ``/latest`` response into one quote per pair."""
    if not isinstance(payload, dict):
        raise FetchError("frankfurter: response was not an object")

    observed = payload.get("date")
    if not isinstance(observed, str):
        raise FetchError("frankfurter: no 'date' in response")
    try:
        observed_date = date.fromisoformat(observed)
    except ValueError as exc:
        raise FetchError(f"frankfurter: bad date {observed!r}") from exc

    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise FetchError("frankfurter: no 'rates' in response")

    base = payload.get("base")
    if isinstance(base, str) and base.upper() != BASE:
        raise FetchError(f"frankfurter: rates are per {base}, expected per {BASE}")

    quotes: list[Quote] = []
    for instrument in instruments:
        value = rates.get(instrument.symbol.upper())
        if isinstance(value, bool) or not isinstance(value, int | float):
            log.warning("fx: no rate for %s", instrument.symbol)
            continue
        quotes.append(
            Quote(
                instrument=instrument.id,
                observed=observed_date,
                value=float(value),
                unit=instrument.unit,
            )
        )

    if not quotes:
        raise FetchError("frankfurter: no configured pair was in the response")
    return quotes
