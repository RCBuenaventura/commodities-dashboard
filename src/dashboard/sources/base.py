"""The contract every price source implements, plus the shared HTTP client.

A source is deliberately dumb: it is handed the instruments that name it, it returns
whatever quotes it could actually retrieve, and it raises :class:`FetchError` when it
cannot. It never decides what to do about a failure — that is the orchestrator's job,
and the orchestrator's rule is to skip and carry on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from dashboard.config import Instrument
    from dashboard.models import Quote

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
