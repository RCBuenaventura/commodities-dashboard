"""Renders ``data/`` into a static ``site/``: ``python -m dashboard.render``.

Entirely offline. Everything the page shows already exists in ``data/`` by the time
this runs, which is what keeps the deploy step from depending on any provider being
up. Charts are inline SVG rather than a client-side charting library, so the page has
no runtime dependencies and works with JavaScript disabled.

Two presentation rules come straight from the project's rules. Every tile states the
date it was observed, so a stale value can never read as today's. And a proxy's
caveat is rendered from config on the tile itself — there is no code path that draws
a proxied instrument without it.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from dashboard.config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from dashboard.models import NewsItem, Series, SeriesPoint
from dashboard.store import (
    DEFAULT_DATA_DIR,
    SourceStatus,
    StoreError,
    read_meta,
    read_news,
    read_series,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dashboard.config import Instrument

log = logging.getLogger("dashboard.render")

DEFAULT_TEMPLATE_DIR: Final = Path("templates")
DEFAULT_STATIC_DIR: Final = Path("static")
DEFAULT_SITE_DIR: Final = Path("site")

#: Points drawn in a tile's sparkline.
SPARK_POINTS: Final = 30
SPARK_WIDTH: Final = 100.0
SPARK_HEIGHT: Final = 28.0

#: How old an observation may get before the tile is badged. Four days covers a
#: weekend plus a bank holiday, which is normal for a futures contract and not
#: something to cry wolf about.
STALE_AFTER_DAYS: Final = 4

#: How long a source may go without a success before it is called out, in hours.
SOURCE_STALE_AFTER_HOURS: Final = 36


@dataclass(frozen=True, slots=True)
class TileView:
    """One instrument, ready to render. All strings are already formatted."""

    id: str
    name: str
    unit: str
    proxy: str | None
    value: str | None
    observed: date | None
    observed_label: str
    change: str | None
    change_pct: str | None
    direction: str
    sterling: str | None
    spark: str | None
    stale: bool
    age_days: int | None
    note: str | None
    news: tuple[NewsItem, ...]

    @property
    def has_value(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class GroupView:
    name: str
    tiles: tuple[TileView, ...]


@dataclass(frozen=True, slots=True)
class PageView:
    groups: tuple[GroupView, ...]
    generated: datetime
    generated_label: str
    problems: tuple[str, ...]
    fx_note: str | None


def build_page(
    config: Config,
    data_dir: Path,
    *,
    today: date | None = None,
) -> PageView:
    """Assemble everything the template needs from what is on disk."""
    today = today or datetime.now(tz=UTC).date()
    statuses = read_meta(data_dir)
    headlines = read_news(data_dir)
    rates = _sterling_rates(config, data_dir)

    groups: list[GroupView] = []
    for group, instruments in config.by_group():
        tiles = tuple(
            _tile(instrument, data_dir, today=today, rates=rates, headlines=headlines)
            for instrument in instruments
        )
        if tiles:
            groups.append(GroupView(name=group.name, tiles=tiles))

    now = datetime.now(tz=UTC)
    return PageView(
        groups=tuple(groups),
        generated=now,
        generated_label=now.strftime("%d %b %Y, %H:%M UTC"),
        problems=_problems(statuses, now),
        fx_note=rates.note,
    )


@dataclass(frozen=True, slots=True)
class _Rates:
    """GBP reference rates, with the fixing date they came from."""

    per_gbp: dict[str, float]
    note: str | None

    def to_sterling(self, value: float, currency: str | None) -> float | None:
        if not currency or currency.upper() == "GBP":
            return None
        rate = self.per_gbp.get(currency.upper())
        if not rate:
            return None
        return value / rate


def _sterling_rates(config: Config, data_dir: Path) -> _Rates:
    """Read the FX instruments back out of the store for the sterling conversion."""
    per_gbp: dict[str, float] = {}
    observed: date | None = None
    for instrument in config.instruments:
        if instrument.source != "fx":
            continue
        series = _series(data_dir, instrument.id)
        if series is None or series.latest is None:
            continue
        per_gbp[instrument.symbol.upper()] = series.latest.value
        observed = max(observed or series.latest.observed, series.latest.observed)

    note = (
        f"Sterling equivalents use the ECB fixing of {observed:%d %b %Y}."
        if per_gbp and observed
        else None
    )
    return _Rates(per_gbp=per_gbp, note=note)


def _tile(
    instrument: Instrument,
    data_dir: Path,
    *,
    today: date,
    rates: _Rates,
    headlines: dict[str, list[NewsItem]],
) -> TileView:
    series = _series(data_dir, instrument.id)
    latest = series.latest if series else None
    previous = series.previous if series else None
    news = tuple(headlines.get(instrument.id, ()))

    if latest is None:
        return TileView(
            id=instrument.id,
            name=instrument.name,
            unit=instrument.unit,
            proxy=instrument.proxy,
            value=None,
            observed=None,
            observed_label="no data yet",
            change=None,
            change_pct=None,
            direction="none",
            sterling=None,
            spark=None,
            stale=False,
            age_days=None,
            note=None,
            news=news,
        )

    age = (today - latest.observed).days
    change = None if previous is None else latest.value - previous.value
    pct = _percent(latest, previous)
    sterling = rates.to_sterling(latest.value, instrument.currency)

    return TileView(
        id=instrument.id,
        name=instrument.name,
        unit=instrument.unit,
        proxy=instrument.proxy,
        value=_number(latest.value, instrument.precision),
        observed=latest.observed,
        observed_label=_observed_label(latest.observed, age),
        change=None if change is None else _signed(change, instrument.precision),
        change_pct=None if pct is None else f"{pct:+.2f}%",
        direction=_direction(change),
        sterling=None
        if sterling is None
        else f"£{_number(sterling, instrument.precision)}",
        spark=_sparkline(series.points if series else ()),
        stale=age > STALE_AFTER_DAYS,
        age_days=age,
        note=None,
        news=news,
    )


def _series(data_dir: Path, instrument_id: str) -> Series | None:
    """Read a history, treating a corrupt file as missing rather than fatal.

    A bad file should cost one tile, not the whole page — the renderer runs in the
    same job as the deploy, and refusing to render would take the site down.
    """
    try:
        return read_series(data_dir, instrument_id)
    except StoreError as exc:
        log.error("%s: unreadable history, rendering as missing — %s", instrument_id, exc)
        return None


def _percent(latest: SeriesPoint, previous: SeriesPoint | None) -> float | None:
    if previous is None or previous.value == 0:
        return None
    return (latest.value - previous.value) / abs(previous.value) * 100


def _direction(change: float | None) -> str:
    if change is None or change == 0:
        return "flat"
    return "up" if change > 0 else "down"


def _number(value: float, precision: int) -> str:
    return f"{value:,.{precision}f}"


def _signed(value: float, precision: int) -> str:
    return f"{value:+,.{precision}f}"


def _observed_label(observed: date, age: int) -> str:
    if age <= 0:
        return "today"
    if age == 1:
        return "yesterday"
    return observed.strftime("%d %b")


def _sparkline(points: Sequence[SeriesPoint]) -> str | None:
    """SVG polyline coordinates for the tail of a series, or ``None`` if too short."""
    tail = list(points)[-SPARK_POINTS:]
    if len(tail) < 2:
        return None

    values = [p.value for p in tail]
    low, high = min(values), max(values)
    span = high - low
    step = SPARK_WIDTH / (len(tail) - 1)

    coords = []
    for index, value in enumerate(values):
        # A flat series would divide by zero; draw it down the middle instead.
        ratio = 0.5 if span == 0 else (value - low) / span
        y = SPARK_HEIGHT - ratio * SPARK_HEIGHT
        coords.append(f"{index * step:.2f},{y:.2f}")
    return " ".join(coords)


def _problems(statuses: dict[str, SourceStatus], now: datetime) -> tuple[str, ...]:
    """Sources worth mentioning on the page, so a silent failure cannot hide."""
    problems: list[str] = []
    for name, status in sorted(statuses.items()):
        if status.last_success is None:
            problems.append(f"{name}: no successful fetch on record")
            continue
        hours = (now - status.last_success).total_seconds() / 3600
        if hours > SOURCE_STALE_AFTER_HOURS:
            days = hours / 24
            problems.append(f"{name}: last succeeded {days:.0f} days ago")
    return tuple(problems)


def render_site(
    config: Config,
    data_dir: Path,
    site_dir: Path,
    *,
    template_dir: Path = DEFAULT_TEMPLATE_DIR,
    static_dir: Path = DEFAULT_STATIC_DIR,
    today: date | None = None,
) -> Path:
    """Render the page and copy the static assets. Returns the written index path."""
    page = build_page(config, data_dir, today=today)

    env = Environment(
        loader=FileSystemLoader(template_dir),
        # Unconditionally, not select_autoescape(): that decides from the file
        # extension, and every template here ends in .j2, which is not on its list.
        # It would silently escape nothing. News titles come from an external feed
        # and go straight onto the page, so this is not a theoretical concern.
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    html = env.get_template("index.html.j2").render(page=page)

    site_dir.mkdir(parents=True, exist_ok=True)
    if static_dir.exists():
        shutil.copytree(static_dir, site_dir, dirs_exist_ok=True)
        _stamp_service_worker(site_dir, page.generated)

    index = site_dir / "index.html"
    index.write_text(html, encoding="utf-8", newline="\n")

    # A .nojekyll file stops GitHub Pages running the output through Jekyll, which
    # would otherwise drop any file or directory beginning with an underscore.
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")

    log.info("wrote %s (%d groups)", index, len(page.groups))
    return index


def _stamp_service_worker(site_dir: Path, generated: datetime) -> None:
    """Give the service worker a fresh cache name for this build.

    Without this the worker keeps serving the previous shell from a cache it has no
    reason to evict, and a home-screen install can sit several updates behind.
    """
    worker = site_dir / "sw.js"
    if not worker.exists():
        return
    stamp = generated.strftime("%Y%m%d%H%M%S")
    source = worker.read_text(encoding="utf-8")
    worker.write_text(
        source.replace("__BUILD_ID__", stamp), encoding="utf-8", newline="\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, prog="python -m dashboard.render"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR)
    parser.add_argument("--static-dir", type=Path, default=DEFAULT_STATIC_DIR)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("configuration is unusable: %s", exc)
        return 2

    render_site(
        config,
        args.data_dir,
        args.site_dir,
        template_dir=args.template_dir,
        static_dir=args.static_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
