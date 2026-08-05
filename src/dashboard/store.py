"""Append-only JSON history — the project's database.

One file per instrument in ``data/``, committed to git. The format is chosen so that
appending a day is a one-line diff: each point is rendered on its own line, so the
daily bot commit reads as a record of what moved.

Two invariants matter here. Appending is **idempotent** — re-running the fetcher on the
same day never duplicates a date. And a series never silently mixes units: if the
configured unit stops matching the stored one, that is a migration, not something to
paper over, so it raises rather than corrupting the history.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from dashboard.models import Quote, Series, SeriesPoint

log = logging.getLogger(__name__)

#: Where the committed history lives, relative to the repository root.
DEFAULT_DATA_DIR: Final = Path("data")

#: Per-source freshness, written alongside the series so the renderer can badge
#: stale tiles without having to guess why a value is old.
META_FILENAME: Final = "_meta.json"

#: Roughly 18 months of trading days. Enough for the charts; small enough that the
#: repository stays a sensible size forever.
MAX_POINTS: Final = 400


class StoreError(Exception):
    """The stored history is unreadable or inconsistent with the configuration."""


class AppendOutcome(StrEnum):
    """What :func:`append_quote` did, so the orchestrator can log it honestly."""

    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """When a source last worked, and what it said when it last did not."""

    source: str
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    last_error: str | None = None


def series_path(data_dir: Path, instrument_id: str) -> Path:
    """Path to one instrument's history file."""
    return data_dir / f"{instrument_id}.json"


def read_series(data_dir: Path, instrument_id: str) -> Series | None:
    """Load one instrument's history, or ``None`` if it has none yet."""
    path = series_path(data_dir, instrument_id)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoreError(f"{path} is unreadable: {exc}") from exc
    return _parse_series(raw, path)


def write_series(data_dir: Path, series: Series) -> None:
    """Write a history file, sorted, pruned and formatted for readable diffs."""
    ordered = tuple(sorted(series.points))[-MAX_POINTS:]
    path = series_path(data_dir, series.instrument)
    _write_atomic(path, _dump_series(Series(series.instrument, series.unit, ordered)))


def append_quote(data_dir: Path, quote: Quote) -> AppendOutcome:
    """Add ``quote`` to its instrument's history, replacing any point for that date.

    Re-running on the same day updates the existing point rather than appending a
    second one. Updating is deliberate: a later run in the day sees a settled price
    where an earlier one saw an intraday value, and the settled one is the better
    record. It is still a real observed value either way — nothing is carried
    forward or interpolated.
    """
    existing = read_series(data_dir, quote.instrument)
    if existing is None:
        existing = Series(instrument=quote.instrument, unit=quote.unit)
    elif existing.unit != quote.unit:
        raise StoreError(
            f"{quote.instrument}: stored history is in {existing.unit!r} but the "
            f"quote is in {quote.unit!r}. Convert or delete "
            f"{series_path(data_dir, quote.instrument)} deliberately; refusing to "
            f"mix units in one series."
        )

    kept = [point for point in existing.points if point.observed != quote.observed]
    previous = next(
        (p for p in existing.points if p.observed == quote.observed),
        None,
    )
    if previous is not None and previous.value == quote.value:
        return AppendOutcome.UNCHANGED

    kept.append(SeriesPoint(observed=quote.observed, value=quote.value))
    write_series(data_dir, Series(quote.instrument, quote.unit, tuple(kept)))
    return AppendOutcome.UPDATED if previous is not None else AppendOutcome.INSERTED


def read_meta(data_dir: Path) -> dict[str, SourceStatus]:
    """Per-source freshness. Missing or corrupt metadata is not fatal.

    Metadata is derived state, not prices: if it is lost the worst outcome is a tile
    that is not badged as stale, so this degrades to an empty mapping with a warning
    rather than failing the run.
    """
    path = data_dir / META_FILENAME
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("ignoring malformed %s: expected an object", path)
        return {}

    sources = raw.get("sources")
    if not isinstance(sources, dict):
        return {}

    statuses: dict[str, SourceStatus] = {}
    for name, entry in sources.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        statuses[name] = SourceStatus(
            source=name,
            last_success=_parse_dt(entry.get("last_success")),
            last_attempt=_parse_dt(entry.get("last_attempt")),
            last_error=entry.get("last_error")
            if isinstance(entry.get("last_error"), str)
            else None,
        )
    return statuses


def write_meta(
    data_dir: Path,
    statuses: dict[str, SourceStatus],
    *,
    generated: datetime | None = None,
) -> None:
    """Write per-source freshness alongside the series files."""
    payload = {
        "generated": (generated or datetime.now(tz=UTC)).isoformat(),
        "sources": {
            name: {
                "last_success": _fmt_dt(status.last_success),
                "last_attempt": _fmt_dt(status.last_attempt),
                "last_error": status.last_error,
            }
            for name, status in sorted(statuses.items())
        },
    }
    _write_atomic(
        data_dir / META_FILENAME,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


# ---- serialisation -------------------------------------------------------------


def _dump_series(series: Series) -> str:
    """Render a series with one point per line, so a daily append is a one-line diff."""
    points = ",\n".join(
        f'    {{"d": "{p.observed.isoformat()}", "v": {_fmt_value(p.value)}}}'
        for p in series.points
    )
    body = f"\n{points}\n  " if points else ""
    return (
        "{\n"
        f'  "instrument": {json.dumps(series.instrument)},\n'
        f'  "unit": {json.dumps(series.unit, ensure_ascii=False)},\n'
        f'  "points": [{body}]\n'
        "}\n"
    )


def _fmt_value(value: float) -> str:
    """Trim binary-float noise so diffs show prices, not 80.67000000000001."""
    return json.dumps(round(value, 6))


def _parse_series(raw: object, path: Path) -> Series:
    if not isinstance(raw, dict):
        raise StoreError(f"{path}: expected an object")
    instrument = raw.get("instrument")
    unit = raw.get("unit")
    points = raw.get("points")
    if not isinstance(instrument, str) or not isinstance(unit, str):
        raise StoreError(f"{path}: missing 'instrument' or 'unit'")
    if not isinstance(points, list):
        raise StoreError(f"{path}: 'points' must be a list")

    parsed: list[SeriesPoint] = []
    for entry in points:
        if not isinstance(entry, dict) or "d" not in entry or "v" not in entry:
            raise StoreError(f"{path}: malformed point {entry!r}")
        parsed.append(
            SeriesPoint(
                observed=_parse_date(entry["d"], path),
                value=_parse_value(entry["v"], path),
            )
        )
    return Series(instrument=instrument, unit=unit, points=tuple(sorted(parsed)))


def _parse_date(value: object, path: Path) -> date:
    if not isinstance(value, str):
        raise StoreError(f"{path}: date {value!r} is not a string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StoreError(f"{path}: bad date {value!r}") from exc


def _parse_value(value: object, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StoreError(f"{path}: value {value!r} is not a number")
    return float(value)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _fmt_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file so an interrupted run cannot truncate history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
