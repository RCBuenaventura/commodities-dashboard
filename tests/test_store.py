"""The JSON history: idempotency, ordering, pruning, and refusal to corrupt itself."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from dashboard.models import Quote, Series, SeriesPoint
from dashboard.store import (
    MAX_POINTS,
    AppendOutcome,
    SourceStatus,
    StoreError,
    append_quote,
    read_meta,
    read_series,
    series_path,
    write_meta,
    write_series,
)


def quote(day: int, value: float, *, unit: str = "USD/bbl") -> Quote:
    return Quote(instrument="brent", observed=date(2026, 8, day), value=value, unit=unit)


def test_append_creates_the_file(tmp_path: Path) -> None:
    assert append_quote(tmp_path, quote(5, 80.67)) is AppendOutcome.INSERTED

    series = read_series(tmp_path, "brent")
    assert series is not None
    assert series.unit == "USD/bbl"
    assert series.points == (SeriesPoint(date(2026, 8, 5), 80.67),)


def test_reading_a_missing_series_returns_none(tmp_path: Path) -> None:
    assert read_series(tmp_path, "brent") is None


def test_appending_the_same_date_twice_does_not_duplicate(tmp_path: Path) -> None:
    append_quote(tmp_path, quote(5, 80.67))
    assert append_quote(tmp_path, quote(5, 80.67)) is AppendOutcome.UNCHANGED

    series = read_series(tmp_path, "brent")
    assert series is not None
    assert len(series.points) == 1


def test_appending_a_new_value_for_today_updates_in_place(tmp_path: Path) -> None:
    append_quote(tmp_path, quote(5, 80.67))
    assert append_quote(tmp_path, quote(5, 81.20)) is AppendOutcome.UPDATED

    series = read_series(tmp_path, "brent")
    assert series is not None
    assert series.points == (SeriesPoint(date(2026, 8, 5), 81.20),)


def test_unchanged_append_does_not_rewrite_the_file(tmp_path: Path) -> None:
    """A no-op run must not produce a git diff."""
    append_quote(tmp_path, quote(5, 80.67))
    path = series_path(tmp_path, "brent")
    before = path.read_bytes()

    append_quote(tmp_path, quote(5, 80.67))
    assert path.read_bytes() == before


def test_points_are_stored_in_date_order(tmp_path: Path) -> None:
    for day in (7, 3, 5):
        append_quote(tmp_path, quote(day, 80.0 + day))

    series = read_series(tmp_path, "brent")
    assert series is not None
    assert [p.observed.day for p in series.points] == [3, 5, 7]


def test_history_is_pruned_to_the_cap(tmp_path: Path) -> None:
    start = date(2024, 1, 1)
    points = tuple(
        SeriesPoint(start + timedelta(days=n), 80.0 + n) for n in range(MAX_POINTS + 50)
    )
    write_series(tmp_path, Series("brent", "USD/bbl", points))

    series = read_series(tmp_path, "brent")
    assert series is not None
    assert len(series.points) == MAX_POINTS
    # Pruning drops the oldest, never the newest.
    assert series.points[-1].observed == points[-1].observed


def test_unit_change_is_refused(tmp_path: Path) -> None:
    """Silently mixing units would make the chart a lie."""
    append_quote(tmp_path, quote(5, 80.67))
    with pytest.raises(StoreError, match="refusing to mix units"):
        append_quote(tmp_path, quote(6, 74.0, unit="EUR/bbl"))


def test_negative_values_round_trip(tmp_path: Path) -> None:
    append_quote(
        tmp_path,
        Quote(
            instrument="uk_agile", observed=date(2026, 8, 5), value=-3.75, unit="p/kWh"
        ),
    )
    series = read_series(tmp_path, "uk_agile")
    assert series is not None
    assert series.points[0].value == -3.75


def test_on_disk_format_matches_the_documented_shape(tmp_path: Path) -> None:
    append_quote(tmp_path, quote(4, 80.67))
    raw = json.loads(series_path(tmp_path, "brent").read_text(encoding="utf-8"))
    assert raw == {
        "instrument": "brent",
        "unit": "USD/bbl",
        "points": [{"d": "2026-08-04", "v": 80.67}],
    }


def test_one_point_per_line_keeps_daily_diffs_small(tmp_path: Path) -> None:
    append_quote(tmp_path, quote(4, 80.67))
    append_quote(tmp_path, quote(5, 81.02))

    lines = series_path(tmp_path, "brent").read_text(encoding="utf-8").splitlines()
    point_lines = [line for line in lines if '"d":' in line]
    assert len(point_lines) == 2


def test_float_noise_is_trimmed(tmp_path: Path) -> None:
    append_quote(tmp_path, quote(5, 0.1 + 0.2))
    assert '"v": 0.3' in series_path(tmp_path, "brent").read_text(encoding="utf-8")


def test_empty_series_writes_valid_json(tmp_path: Path) -> None:
    write_series(tmp_path, Series("brent", "USD/bbl"))
    raw = json.loads(series_path(tmp_path, "brent").read_text(encoding="utf-8"))
    assert raw["points"] == []


def test_corrupt_history_raises_rather_than_starting_over(tmp_path: Path) -> None:
    """Losing price history to a parse error would be silent data loss."""
    series_path(tmp_path, "brent").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="unreadable"):
        read_series(tmp_path, "brent")


@pytest.mark.parametrize(
    "points",
    [
        pytest.param("{}", id="not-a-list"),
        pytest.param('[{"d": "nope", "v": 1}]', id="unparseable-date"),
        pytest.param('[{"d": "2026-08-05"}]', id="no-value"),
        pytest.param('[{"d": "2026-08-05", "v": "x"}]', id="value-not-a-number"),
        pytest.param('[{"d": "2026-08-05", "v": true}]', id="value-is-a-bool"),
    ],
)
def test_malformed_points_are_rejected(tmp_path: Path, points: str) -> None:
    series_path(tmp_path, "brent").write_text(
        f'{{"instrument": "brent", "unit": "USD/bbl", "points": {points}}}',
        encoding="utf-8",
    )
    with pytest.raises(StoreError):
        read_series(tmp_path, "brent")


def test_history_without_a_unit_is_rejected(tmp_path: Path) -> None:
    series_path(tmp_path, "brent").write_text(
        '{"instrument": "brent", "points": []}', encoding="utf-8"
    )
    with pytest.raises(StoreError, match="missing"):
        read_series(tmp_path, "brent")


def test_no_temporary_files_are_left_behind(tmp_path: Path) -> None:
    append_quote(tmp_path, quote(5, 80.67))
    assert [p.name for p in tmp_path.iterdir()] == ["brent.json"]


# ---- metadata ----


def test_meta_round_trips(tmp_path: Path) -> None:
    now = datetime(2026, 8, 5, 6, 12, tzinfo=UTC)
    write_meta(
        tmp_path,
        {
            "yahoo": SourceStatus("yahoo", last_success=now, last_attempt=now),
            "eia": SourceStatus("eia", last_attempt=now, last_error="HTTP 503"),
        },
        generated=now,
    )

    meta = read_meta(tmp_path)
    assert meta["yahoo"].last_success == now
    assert meta["yahoo"].last_error is None
    assert meta["eia"].last_success is None
    assert meta["eia"].last_error == "HTTP 503"


def test_missing_meta_is_empty(tmp_path: Path) -> None:
    assert read_meta(tmp_path) == {}


def test_corrupt_meta_degrades_quietly(tmp_path: Path) -> None:
    """Metadata is derived state; losing it must not fail the daily run."""
    (tmp_path / "_meta.json").write_text("{not json", encoding="utf-8")
    assert read_meta(tmp_path) == {}
