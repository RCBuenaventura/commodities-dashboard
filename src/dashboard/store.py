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
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from dashboard.models import NewsItem, Quote, Series, SeriesPoint

log = logging.getLogger(__name__)

#: Where the committed history lives, relative to the repository root.
DEFAULT_DATA_DIR: Final = Path("data")

#: Per-source freshness, written alongside the series so the renderer can badge
#: stale tiles without having to guess why a value is old.
META_FILENAME: Final = "_meta.json"

#: The day's headlines, replaced on each run rather than appended to.
NEWS_FILENAME: Final = "news.json"

#: Roughly 18 months of trading days. Enough for the charts; small enough that the
#: repository stays a sensible size forever.
MAX_POINTS: Final = 400

#: Decimal places kept on disk. Enough for every unit here, and it trims the binary
#: float noise that would otherwise put 80.67000000000001 in a diff.
#:
#: Rounding happens on the way *in*, not just at serialisation. A stored 71.989998
#: compared against a freshly fetched 71.98999786376953 differs, so an unrounded
#: comparison would report every point as changed on every run and rewrite files
#: that are already correct.
STORED_PRECISION: Final = 6


def _store_value(value: float) -> float:
    """The value as it will exist on disk, for storing and for comparing."""
    return round(value, STORED_PRECISION)


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

    value = _store_value(quote.value)
    kept = [point for point in existing.points if point.observed != quote.observed]
    previous = next(
        (p for p in existing.points if p.observed == quote.observed),
        None,
    )
    if previous is not None and previous.value == value:
        return AppendOutcome.UNCHANGED

    kept.append(SeriesPoint(observed=quote.observed, value=value))
    write_series(data_dir, Series(quote.instrument, quote.unit, tuple(kept)))
    return AppendOutcome.UPDATED if previous is not None else AppendOutcome.INSERTED


def append_quotes(data_dir: Path, quotes: Iterable[Quote]) -> dict[AppendOutcome, int]:
    """Append many quotes, touching each instrument's file at most once.

    Sources backfill: a first run returns a month of bars per instrument. Appending
    those one at a time would read and rewrite the same file for every point, so
    quotes are grouped by instrument and each history is written once. The outcome
    counts are per quote, so the caller can log what actually changed.
    """
    grouped: defaultdict[str, list[Quote]] = defaultdict(list)
    for quote in quotes:
        grouped[quote.instrument].append(quote)

    tally: dict[AppendOutcome, int] = dict.fromkeys(AppendOutcome, 0)

    for instrument_id, batch in grouped.items():
        unit = batch[0].unit
        mixed = {q.unit for q in batch}
        if len(mixed) > 1:
            raise StoreError(
                f"{instrument_id}: one run produced quotes in {sorted(mixed)}"
            )

        existing = read_series(data_dir, instrument_id)
        if existing is None:
            existing = Series(instrument=instrument_id, unit=unit)
        elif existing.unit != unit:
            raise StoreError(
                f"{instrument_id}: stored history is in {existing.unit!r} but the "
                f"quote is in {unit!r}. Convert or delete "
                f"{series_path(data_dir, instrument_id)} deliberately; refusing to "
                f"mix units in one series."
            )

        by_date = {point.observed: point.value for point in existing.points}
        for quote in batch:
            value = _store_value(quote.value)
            before = by_date.get(quote.observed)
            if before is None:
                tally[AppendOutcome.INSERTED] += 1
            elif before == value:
                tally[AppendOutcome.UNCHANGED] += 1
                continue
            else:
                tally[AppendOutcome.UPDATED] += 1
            by_date[quote.observed] = value

        points = tuple(
            SeriesPoint(observed=day, value=value) for day, value in by_date.items()
        )
        if points != existing.points:
            write_series(data_dir, Series(instrument_id, unit, points))

    return tally


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


def write_news(data_dir: Path, headlines: dict[str, list[NewsItem]]) -> None:
    """Write the day's headlines, keyed by instrument.

    Unlike prices, news is replaced rather than appended: a stale headline is not a
    data point, it is just old news, and keeping a year of them in git would bury
    the price diffs that make the daily commit readable.
    """
    payload = {
        "generated": datetime.now(tz=UTC).isoformat(),
        "instruments": {
            instrument_id: [
                {
                    "title": item.title,
                    "url": item.url,
                    "source": item.source,
                    "published": _fmt_dt(item.published),
                }
                for item in items
            ]
            for instrument_id, items in sorted(headlines.items())
        },
    }
    _write_atomic(
        data_dir / NEWS_FILENAME,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def read_news(data_dir: Path) -> dict[str, list[NewsItem]]:
    """Read stored headlines. Missing or corrupt news degrades to nothing."""
    path = data_dir / NEWS_FILENAME
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable %s: %s", path, exc)
        return {}

    instruments = raw.get("instruments") if isinstance(raw, dict) else None
    if not isinstance(instruments, dict):
        return {}

    headlines: dict[str, list[NewsItem]] = {}
    for instrument_id, items in instruments.items():
        if not isinstance(instrument_id, str) or not isinstance(items, list):
            continue
        parsed = [
            NewsItem(
                title=str(item["title"]),
                url=str(item["url"]),
                source=str(item.get("source", "")),
                published=_parse_dt(item.get("published")),
            )
            for item in items
            if isinstance(item, dict) and item.get("title") and item.get("url")
        ]
        if parsed:
            headlines[instrument_id] = parsed
    return headlines


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
    """Render at the stored precision, so disk and memory always agree."""
    return json.dumps(_store_value(value))


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
