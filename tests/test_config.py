"""Config loading, and guards on the real `instruments.toml`.

The strictness tests matter more than they look: `instruments.toml` is the one file
a person edits to add a commodity, so a typo there has to fail loudly rather than
silently dropping a tile from the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.config import Config, ConfigError, load_config

VALID = """
[[group]]
id = "crude"
name = "Crude & refined"

[[instrument]]
id = "brent"
name = "Brent crude"
group = "crude"
source = "yahoo"
symbol = "BZ=F"
unit = "USD/bbl"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "instruments.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_a_minimal_file(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, VALID))
    assert [i.id for i in config.instruments] == ["brent"]
    assert config.instruments[0].precision == 2
    assert config.instruments[0].proxy is None


def test_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no config file"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(write(tmp_path, "[[instrument]\nid = "))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown keys"):
        load_config(write(tmp_path, VALID + '\nsymbl = "typo"\n'))


def test_missing_required_key_is_rejected(tmp_path: Path) -> None:
    without_unit = VALID.replace('unit = "USD/bbl"', "")
    with pytest.raises(ConfigError, match="missing required key 'unit'"):
        load_config(write(tmp_path, without_unit))


def test_duplicate_instrument_id_is_rejected(tmp_path: Path) -> None:
    twice = (
        VALID
        + """
[[instrument]]
id = "brent"
name = "Brent crude again"
group = "crude"
source = "yahoo"
symbol = "BZ=F"
unit = "USD/bbl"
"""
    )
    with pytest.raises(ConfigError, match="duplicate id 'brent'"):
        load_config(write(tmp_path, twice))


def test_unknown_group_is_rejected(tmp_path: Path) -> None:
    wrong_group = VALID.replace('group = "crude"', 'group = "liquids"')
    with pytest.raises(ConfigError, match="unknown group"):
        load_config(write(tmp_path, wrong_group))


def test_no_instruments_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="defines no instruments"):
        load_config(write(tmp_path, '[[group]]\nid = "crude"\nname = "Crude"\n'))


def test_unexpected_top_level_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unexpected top-level keys"):
        load_config(write(tmp_path, VALID + '\n[settings]\ntheme = "dark"\n'))


def test_precision_must_be_a_plain_integer(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="precision"):
        load_config(write(tmp_path, VALID + "precision = true\n"))


def test_empty_string_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        load_config(write(tmp_path, VALID.replace('symbol = "BZ=F"', 'symbol = ""')))


# ---- the real file ----


@pytest.fixture
def real_config(repo_root: Path) -> Config:
    return load_config(repo_root / "instruments.toml")


def test_real_instruments_file_is_valid(real_config: Config) -> None:
    assert len(real_config.instruments) > 10
    assert {i.source for i in real_config.instruments} <= {
        "yahoo",
        "eia",
        "elexon",
        "octopus",
        "carbon",
        "fx",
    }


def test_every_instrument_belongs_to_a_rendered_group(real_config: Config) -> None:
    """Nothing may be configured into a group the page does not display."""
    grouped = {i.id for _, members in real_config.by_group() for i in members}
    assert grouped == {i.id for i in real_config.instruments}


def test_known_proxies_carry_their_caveat(real_config: Config) -> None:
    """The three documented stand-ins must each explain themselves.

    This is the honesty rule expressed as a test: if someone removes a caveat to
    tidy up a tile, the suite fails rather than the dashboard quietly implying it
    has a price it cannot source.
    """
    for instrument_id in ("nbp", "jkm", "aluminium"):
        instrument = real_config.get(instrument_id)
        assert instrument.proxy, f"{instrument_id} must document what it stands in for"

    assert "ICE" in (real_config.get("nbp").proxy or "")
    assert "Platts" in (real_config.get("jkm").proxy or "")
    assert "LME" in (real_config.get("aluminium").proxy or "")


def test_by_source_buckets_every_instrument(real_config: Config) -> None:
    buckets = real_config.by_source()
    assert sum(len(v) for v in buckets.values()) == len(real_config.instruments)
    assert "yahoo" in buckets


def test_get_raises_for_unknown_id(real_config: Config) -> None:
    with pytest.raises(KeyError):
        real_config.get("unobtanium")
