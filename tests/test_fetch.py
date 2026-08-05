"""The orchestrator: fail-soft behaviour and the exit code contract.

These use stub sources rather than the real ones. What is under test is the policy —
what happens when a source dies — not any provider's payload.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from conftest import instrument
from dashboard.config import Config, Group, Instrument
from dashboard.fetch import build_sources, run
from dashboard.models import Quote
from dashboard.sources.base import FetchError, Source
from dashboard.store import read_meta, read_series


class StubSource:
    """A source that returns what it was told to, or raises what it was told to."""

    def __init__(
        self,
        source_id: str,
        quotes: list[Quote] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.id = source_id
        self._quotes = quotes or []
        self._error = error
        self.calls = 0

    def fetch(self, instruments: object) -> list[Quote]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._quotes


def config_for(*instruments: Instrument) -> Config:
    groups = tuple(
        dict.fromkeys(Group(id=i.group, name=i.group.title()) for i in instruments)
    )
    return Config(groups=groups, instruments=instruments)


def quote(instrument_id: str, day: int, value: float, unit: str = "USD/bbl") -> Quote:
    return Quote(
        instrument=instrument_id, observed=date(2026, 8, day), value=value, unit=unit
    )


BRENT = instrument(id="brent", source="alpha")
COPPER = instrument(id="copper", source="beta", unit="USD/lb")


def test_a_failing_source_does_not_stop_the_others(tmp_path: Path) -> None:
    """The rule the whole design exists for."""
    sources: dict[str, Source] = {
        "alpha": StubSource("alpha", error=FetchError("provider is down")),
        "beta": StubSource("beta", [quote("copper", 5, 6.7, "USD/lb")]),
    }

    code = run(config_for(BRENT, COPPER), tmp_path, sources=sources, with_news=False)

    assert code == 0
    assert read_series(tmp_path, "copper") is not None
    assert read_series(tmp_path, "brent") is None


def test_exit_is_non_zero_only_when_everything_failed(tmp_path: Path) -> None:
    sources: dict[str, Source] = {
        "alpha": StubSource("alpha", error=FetchError("down")),
        "beta": StubSource("beta", error=FetchError("also down")),
    }

    assert run(config_for(BRENT, COPPER), tmp_path, sources=sources, with_news=False) == 1


def test_an_unexpected_exception_is_contained(tmp_path: Path) -> None:
    """A bug in one source must not take the run down with it."""
    sources: dict[str, Source] = {
        "alpha": StubSource("alpha", error=ValueError("bug in a parser")),
        "beta": StubSource("beta", [quote("copper", 5, 6.7, "USD/lb")]),
    }

    assert run(config_for(BRENT, COPPER), tmp_path, sources=sources, with_news=False) == 0
    assert read_series(tmp_path, "copper") is not None


def test_failure_is_recorded_without_losing_the_last_success(tmp_path: Path) -> None:
    """Staleness is only measurable if the last success survives the failure."""
    working: dict[str, Source] = {"alpha": StubSource("alpha", [quote("brent", 4, 79.0)])}
    run(config_for(BRENT), tmp_path, sources=working, with_news=False)
    first = read_meta(tmp_path)["alpha"].last_success
    assert first is not None

    broken: dict[str, Source] = {
        "alpha": StubSource("alpha", error=FetchError("provider is down"))
    }
    run(config_for(BRENT), tmp_path, sources=broken, with_news=False)

    after = read_meta(tmp_path)["alpha"]
    assert after.last_success == first
    assert after.last_error is not None
    assert "provider is down" in after.last_error
    assert after.last_attempt is not None and after.last_attempt > first


def test_a_failing_source_never_overwrites_good_history(tmp_path: Path) -> None:
    """Rule one: a dead provider leaves the last real value alone."""
    run(
        config_for(BRENT),
        tmp_path,
        sources={"alpha": StubSource("alpha", [quote("brent", 4, 79.0)])},
        with_news=False,
    )
    run(
        config_for(BRENT),
        tmp_path,
        sources={"alpha": StubSource("alpha", error=FetchError("down"))},
        with_news=False,
    )

    series = read_series(tmp_path, "brent")
    assert series is not None
    assert series.latest is not None
    assert series.latest.value == 79.0


def test_an_unknown_source_id_is_reported_not_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo in instruments.toml should be loud, not a quietly missing tile."""
    with caplog.at_level("ERROR"):
        code = run(
            config_for(instrument(id="brent", source="typo")),
            tmp_path,
            sources={},
            with_news=False,
        )

    assert code == 0
    assert any("typo" in record.getMessage() for record in caplog.records)


def test_meta_is_written_even_when_a_source_fails(tmp_path: Path) -> None:
    run(
        config_for(BRENT),
        tmp_path,
        sources={"alpha": StubSource("alpha", error=FetchError("down"))},
        with_news=False,
    )
    assert (tmp_path / "_meta.json").exists()


def test_rerunning_is_idempotent(tmp_path: Path) -> None:
    quotes = [quote("brent", 4, 79.0), quote("brent", 5, 80.0)]
    config = config_for(BRENT)

    run(config, tmp_path, sources={"alpha": StubSource("alpha", quotes)}, with_news=False)
    first = (tmp_path / "brent.json").read_bytes()

    run(config, tmp_path, sources={"alpha": StubSource("alpha", quotes)}, with_news=False)
    assert (tmp_path / "brent.json").read_bytes() == first


def test_every_configured_source_id_actually_exists() -> None:
    """Guards against a source module being renamed out from under the config."""
    from dashboard.config import load_config

    config = load_config(Path(__file__).resolve().parents[1] / "instruments.toml")
    assert set(config.by_source()) <= set(build_sources())
