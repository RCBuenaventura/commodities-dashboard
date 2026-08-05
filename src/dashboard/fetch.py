"""The daily orchestrator: ``python -m dashboard.fetch``.

Fetches every configured source, appends what came back to ``data/``, and records
per-source freshness in ``data/_meta.json``.

The governing rule is that one broken provider must not cost the other fifteen
instruments their update. Every source runs inside its own ``try``, a failure logs a
warning and moves on, and the run still exits zero. The exit code is non-zero only if
*every* source failed, which means something systemic — no network, a bad config —
rather than one flaky endpoint.

Nothing here ever invents a value. A source that returns nothing simply leaves its
instruments at their last real observation, and the renderer badges them stale.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dashboard.config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from dashboard.sources.base import FetchError, Source
from dashboard.sources.carbon import CarbonSource
from dashboard.sources.eia import EIASource
from dashboard.sources.elexon import ElexonSource
from dashboard.sources.fx import FXSource
from dashboard.sources.news import NewsSource
from dashboard.sources.octopus import OctopusSource
from dashboard.sources.yahoo import YahooSource
from dashboard.store import (
    DEFAULT_DATA_DIR,
    AppendOutcome,
    SourceStatus,
    StoreError,
    append_quotes,
    read_meta,
    write_meta,
    write_news,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger("dashboard.fetch")


def build_sources() -> dict[str, Source]:
    """Every available source, keyed by the id `instruments.toml` refers to."""
    sources: Sequence[Source] = (
        YahooSource(),
        EIASource(),
        ElexonSource(),
        OctopusSource(),
        CarbonSource(),
        FXSource(),
    )
    return {source.id: source for source in sources}


def run(
    config: Config,
    data_dir: Path,
    *,
    sources: dict[str, Source] | None = None,
    with_news: bool = True,
) -> int:
    """Fetch everything and write it. Returns the process exit code.

    Zero when at least one source produced data, one when every source failed.
    """
    available = sources if sources is not None else build_sources()
    statuses = read_meta(data_dir)
    started = datetime.now(tz=UTC)

    attempted = 0
    succeeded = 0

    for source_id, instruments in sorted(config.by_source().items()):
        source = available.get(source_id)
        if source is None:
            # A config typo, not a provider outage: name it loudly and carry on.
            log.error(
                "%s: no such source (instruments: %s)",
                source_id,
                ", ".join(i.id for i in instruments),
            )
            continue

        attempted += 1
        try:
            quotes = source.fetch(instruments)
        except FetchError as exc:
            log.warning("%s: skipped — %s", source_id, exc)
            statuses[source_id] = _failed(
                statuses.get(source_id), source_id, exc, started
            )
            continue
        except Exception as exc:
            # A bug in one source must not take the other five down with it.
            log.exception("%s: unexpected error, skipping", source_id)
            statuses[source_id] = _failed(
                statuses.get(source_id), source_id, exc, started
            )
            continue

        try:
            tally = append_quotes(data_dir, quotes)
        except StoreError as exc:
            # The fetch worked; the history refused it. That is a real problem worth
            # surfacing, but it is still only one source.
            log.error("%s: could not store quotes — %s", source_id, exc)
            statuses[source_id] = _failed(
                statuses.get(source_id), source_id, exc, started
            )
            continue

        succeeded += 1
        statuses[source_id] = SourceStatus(
            source=source_id,
            last_success=started,
            last_attempt=started,
            last_error=None,
        )
        log.info(
            "%s: %d quotes (%d new, %d updated, %d unchanged)",
            source_id,
            len(quotes),
            tally[AppendOutcome.INSERTED],
            tally[AppendOutcome.UPDATED],
            tally[AppendOutcome.UNCHANGED],
        )

    if with_news:
        _fetch_news(config, data_dir)

    write_meta(data_dir, statuses, generated=started)

    if attempted and not succeeded:
        log.error("every source failed; leaving the existing history untouched")
        return 1
    log.info("%d of %d sources updated", succeeded, attempted)
    return 0


def _fetch_news(config: Config, data_dir: Path) -> None:
    """Headlines are decoration. They never affect the exit code."""
    try:
        headlines = NewsSource().fetch(list(config.instruments))
        write_news(data_dir, headlines)
        log.info("news: %d instruments with headlines", len(headlines))
    except Exception:
        # Never let a news failure break a price run.
        log.exception("news: skipped")


def _failed(
    previous: SourceStatus | None,
    source_id: str,
    exc: BaseException,
    when: datetime,
) -> SourceStatus:
    """Record the failure but keep the last success, so staleness stays measurable."""
    return SourceStatus(
        source=source_id,
        last_success=previous.last_success if previous else None,
        last_attempt=when,
        last_error=f"{type(exc).__name__}: {exc}",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, prog="python -m dashboard.fetch"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--no-news", action="store_true", help="skip the news feeds")
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

    return run(config, args.data_dir, with_news=not args.no_news)


if __name__ == "__main__":
    raise SystemExit(main())
