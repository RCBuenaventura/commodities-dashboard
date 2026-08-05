"""Loads `instruments.toml` into typed objects.

This file is the whole extension mechanism: adding a commodity that an existing
provider can serve must be an edit here and nowhere else. That only holds if the
loader is strict, so unknown keys, missing keys, duplicate ids and unknown groups
are all hard errors rather than silently ignored — a typo in a symbol should fail
the run, not quietly drop a tile.
"""

from __future__ import annotations

import tomllib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

#: Where `instruments.toml` lives relative to the repository root.
DEFAULT_CONFIG_PATH: Final = Path("instruments.toml")


class ConfigError(Exception):
    """`instruments.toml` is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Group:
    """A display grouping of instruments; order in the file is display order."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Instrument:
    """One tracked commodity, rate or index."""

    id: str
    name: str
    group: str
    source: str
    symbol: str
    unit: str
    currency: str | None = None
    precision: int = 2
    #: Set when this instrument stands in for a contract with no free feed. The
    #: renderer shows it verbatim; it is the mechanism behind the honesty rule, so
    #: it is carried in config rather than hardcoded in a template.
    proxy: str | None = None
    #: Search term for the per-instrument news feed. ``None`` means no feed.
    news: str | None = None


@dataclass(frozen=True, slots=True)
class Config:
    """The parsed contents of `instruments.toml`."""

    groups: tuple[Group, ...]
    instruments: tuple[Instrument, ...]

    def by_source(self) -> dict[str, list[Instrument]]:
        """Instruments bucketed by the source that serves them."""
        buckets: defaultdict[str, list[Instrument]] = defaultdict(list)
        for instrument in self.instruments:
            buckets[instrument.source].append(instrument)
        return dict(buckets)

    def by_group(self) -> list[tuple[Group, list[Instrument]]]:
        """Groups in file order, each with its instruments in file order."""
        return [
            (group, [i for i in self.instruments if i.group == group.id])
            for group in self.groups
        ]

    def get(self, instrument_id: str) -> Instrument:
        """Look up one instrument by id."""
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                return instrument
        raise KeyError(instrument_id)


def load_config(path: Path | None = None) -> Config:
    """Read and validate `instruments.toml`.

    Raises :class:`ConfigError` with a message naming the offending entry for any
    problem, so a bad edit is obvious from the CI log alone.
    """
    path = path or DEFAULT_CONFIG_PATH
    try:
        with path.open("rb") as handle:
            raw: dict[str, Any] = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"no config file at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    groups = tuple(_group(table, i) for i, table in enumerate(_tables(raw, "group")))
    instruments = tuple(
        _instrument(table, i) for i, table in enumerate(_tables(raw, "instrument"))
    )

    if unexpected := set(raw) - {"group", "instrument"}:
        raise ConfigError(f"{path}: unexpected top-level keys {sorted(unexpected)}")
    if not instruments:
        raise ConfigError(f"{path}: defines no instruments")

    _check_unique(group.id for group in groups)
    _check_unique(instrument.id for instrument in instruments)

    known = {group.id for group in groups}
    for instrument in instruments:
        if instrument.group not in known:
            raise ConfigError(
                f"instrument {instrument.id!r} is in unknown group "
                f"{instrument.group!r} (known: {sorted(known)})"
            )

    return Config(groups=groups, instruments=instruments)


def _tables(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConfigError(f"[[{key}]] must be an array of tables")
    return list(value)


def _group(table: dict[str, Any], index: int) -> Group:
    where = f"[[group]] #{index + 1}"
    remaining = dict(table)
    group = Group(
        id=_req_str(remaining, "id", where), name=_req_str(remaining, "name", where)
    )
    _reject_extra(remaining, where)
    return group


def _instrument(table: dict[str, Any], index: int) -> Instrument:
    where = f"[[instrument]] #{index + 1}"
    remaining = dict(table)
    identifier = _req_str(remaining, "id", where)
    where = f"instrument {identifier!r}"
    instrument = Instrument(
        id=identifier,
        name=_req_str(remaining, "name", where),
        group=_req_str(remaining, "group", where),
        source=_req_str(remaining, "source", where),
        symbol=_req_str(remaining, "symbol", where),
        unit=_req_str(remaining, "unit", where),
        currency=_opt_str(remaining, "currency", where),
        precision=_opt_int(remaining, "precision", where, default=2),
        proxy=_opt_str(remaining, "proxy", where),
        news=_opt_str(remaining, "news", where),
    )
    _reject_extra(remaining, where)
    return instrument


def _req_str(table: dict[str, Any], key: str, where: str) -> str:
    if key not in table:
        raise ConfigError(f"{where}: missing required key {key!r}")
    value = table.pop(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: {key!r} must be a non-empty string")
    return value


def _opt_str(table: dict[str, Any], key: str, where: str) -> str | None:
    if key not in table:
        return None
    value = table.pop(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: {key!r} must be a non-empty string when present")
    return value


def _opt_int(table: dict[str, Any], key: str, where: str, *, default: int) -> int:
    if key not in table:
        return default
    value = table.pop(key)
    # bool is an int subclass; precision = true is a mistake, not a 1.
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 8:
        raise ConfigError(f"{where}: {key!r} must be an integer between 0 and 8")
    return value


def _reject_extra(remaining: dict[str, Any], where: str) -> None:
    if remaining:
        raise ConfigError(f"{where}: unknown keys {sorted(remaining)}")


def _check_unique(ids: Iterable[str]) -> None:
    seen: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            raise ConfigError(f"duplicate id {identifier!r}")
        seen.add(identifier)
