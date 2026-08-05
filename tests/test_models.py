"""Domain model behaviour, mostly the guards that keep bad numbers out of `data/`."""

from __future__ import annotations

from datetime import date

import pytest

from dashboard.models import Quote, Series, SeriesPoint


def test_quote_rejects_nan() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        Quote(
            instrument="brent",
            observed=date(2026, 8, 5),
            value=float("nan"),
            unit="USD/bbl",
        )


def test_quote_rejects_infinity() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        Quote(
            instrument="brent",
            observed=date(2026, 8, 5),
            value=float("inf"),
            unit="USD/bbl",
        )


def test_quote_rejects_empty_instrument() -> None:
    with pytest.raises(ValueError, match="no instrument"):
        Quote(instrument="", observed=date(2026, 8, 5), value=80.0, unit="USD/bbl")


def test_quote_allows_negative_values() -> None:
    """UK power goes negative. Rejecting it would drop real observations."""
    quote = Quote(
        instrument="uk_agile", observed=date(2026, 8, 5), value=-3.75, unit="p/kWh"
    )
    assert quote.value == -3.75


def test_quote_is_frozen() -> None:
    quote = Quote(instrument="wti", observed=date(2026, 8, 5), value=77.1, unit="USD/bbl")
    with pytest.raises(AttributeError):
        quote.value = 0.0  # type: ignore[misc]


def test_series_latest_and_previous() -> None:
    series = Series(
        instrument="brent",
        unit="USD/bbl",
        points=(
            SeriesPoint(date(2026, 8, 3), 79.0),
            SeriesPoint(date(2026, 8, 4), 80.0),
            SeriesPoint(date(2026, 8, 5), 81.0),
        ),
    )
    assert series.latest == SeriesPoint(date(2026, 8, 5), 81.0)
    assert series.previous == SeriesPoint(date(2026, 8, 4), 80.0)


def test_series_handles_short_history() -> None:
    empty = Series(instrument="brent", unit="USD/bbl")
    assert empty.latest is None
    assert empty.previous is None

    one = Series("brent", "USD/bbl", (SeriesPoint(date(2026, 8, 5), 81.0),))
    assert one.latest is not None
    assert one.previous is None


def test_series_points_sort_by_date() -> None:
    unsorted = [
        SeriesPoint(date(2026, 8, 5), 81.0),
        SeriesPoint(date(2026, 8, 3), 79.0),
        SeriesPoint(date(2026, 8, 4), 80.0),
    ]
    assert [p.observed.day for p in sorted(unsorted)] == [3, 4, 5]
