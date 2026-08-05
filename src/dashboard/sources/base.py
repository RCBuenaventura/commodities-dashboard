"""The contract every price source implements, plus the shared HTTP client.

A source is deliberately dumb: it is handed the instruments that name it, it returns
whatever quotes it could actually retrieve, and it raises :class:`FetchError` when it
cannot. It never decides what to do about a failure — that is the orchestrator's job,
and the orchestrator's rule is to skip and carry on.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from dashboard.config import Instrument
    from dashboard.models import Quote

log = logging.getLogger(__name__)

#: Generous enough for a slow provider, short enough that one hung source cannot
#: stall the daily run. Every request in the project carries an explicit timeout.
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)

#: Yahoo's chart endpoint is undocumented and returns 403 to the default httpx
#: user-agent. The other providers are indifferent to it.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


class FetchError(Exception):
    """A source could not produce data and should be skipped for this run.

    Raised for anything the orchestrator should treat as this-source-is-down:
    transport errors, non-2xx responses, unparseable payloads, missing fields.
    It must never be raised to mean "the value looked wrong so I substituted one".
    """


@runtime_checkable
class Source(Protocol):
    """A provider of quotes for the instruments configured against it."""

    #: Matches the ``source`` key in `instruments.toml`.
    id: str

    def fetch(self, instruments: Sequence[Instrument]) -> list[Quote]:
        """Return quotes for whichever of ``instruments`` this source could retrieve.

        Returning fewer quotes than instruments is normal and not an error: a market
        that has not settled yet simply has no value today. Raise :class:`FetchError`
        only when the source as a whole is unusable.
        """
        ...


def build_client(*, timeout: httpx.Timeout | None = None) -> httpx.Client:
    """An `httpx` client configured the way every source in this project needs it."""
    return httpx.Client(
        timeout=timeout or DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str | int] | None = None,
    context: str,
) -> object:
    """GET and decode JSON, turning every transport-level failure into `FetchError`.

    Every way an HTTP call can fail is a reason to skip the source, so they all
    become the same exception here rather than being re-handled in six modules.
    """
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise FetchError(
            f"{context}: HTTP {exc.response.status_code} from {exc.request.url.host}"
        ) from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"{context}: {type(exc).__name__}: {exc}") from exc
    except ValueError as exc:
        raise FetchError(f"{context}: response was not valid JSON") from exc


#: UK electricity is settled in half-hour periods; a full day is 48 of them.
SETTLEMENT_PERIODS_PER_DAY: Final = 48

#: How much of a day must be present before its average means anything. A day
#: missing a couple of periods still averages sensibly; a half-populated day does
#: not, and publishing its mean as "the daily price" would misrepresent it.
MIN_DAY_COVERAGE: Final = 0.85


def daily_average(
    values: Sequence[float],
    *,
    label: str,
    expected: int = SETTLEMENT_PERIODS_PER_DAY,
) -> float | None:
    """Mean of a day of half-hourly readings, or ``None`` if the day is too sparse.

    Returning ``None`` rather than a number is the point: a partial day averaged
    and published as the daily figure is a plausible wrong value, which is exactly
    what this project refuses to show. The caller skips the day instead.
    """
    if not values:
        return None
    if len(values) < expected * MIN_DAY_COVERAGE:
        log.info(
            "%s: only %d of %d periods present, skipping the day",
            label,
            len(values),
            expected,
        )
        return None
    return sum(values) / len(values)
