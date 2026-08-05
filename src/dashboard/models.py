"""Domain types.

Everything here is a frozen dataclass and every date is a :class:`datetime.date`.
The only place a date becomes a string is the JSON boundary in :mod:`dashboard.store`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Quote:
    """One value a source actually returned, for one instrument, on one day.

    Non-finite values are rejected at construction. A ``NaN`` that reached the store
    would be written to ``data/`` and rendered as though it were a price, which is
    the one failure mode this project treats as unacceptable — better to raise here
    and let the orchestrator skip the source.

    Negative values are legitimate and must not be rejected: UK wholesale power and
    the Octopus Agile rate both go below zero when the grid is long.
    """

    instrument: str
    observed: date
    value: float
    unit: str

    def __post_init__(self) -> None:
        if not self.instrument:
            raise ValueError("quote has no instrument id")
        if not math.isfinite(self.value):
            raise ValueError(
                f"{self.instrument}: refusing non-finite value {self.value!r}"
            )


@dataclass(frozen=True, slots=True, order=True)
class SeriesPoint:
    """A single dated observation in a stored history."""

    observed: date
    value: float


@dataclass(frozen=True, slots=True)
class Series:
    """The full stored history for one instrument."""

    instrument: str
    unit: str
    points: tuple[SeriesPoint, ...] = ()

    @property
    def latest(self) -> SeriesPoint | None:
        """The most recent point, or ``None`` for an empty history."""
        return self.points[-1] if self.points else None

    @property
    def previous(self) -> SeriesPoint | None:
        """The point before :attr:`latest`, used for the day-on-day change."""
        return self.points[-2] if len(self.points) > 1 else None


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One headline for one instrument group.

    ``published`` is timezone-aware when the feed supplies a parseable date and
    ``None`` when it does not; it is never guessed at.
    """

    title: str
    url: str
    source: str
    published: datetime | None = None
