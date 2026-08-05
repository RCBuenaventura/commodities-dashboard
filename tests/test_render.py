"""The renderer: view-model arithmetic, staleness, and the honesty guarantees.

The template tests render the real templates against a small config, because the
rules that matter — a proxy always shows its caveat, a value always shows its date —
are properties of the output, not of any function.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from conftest import instrument
from dashboard.config import Config, Group, Instrument
from dashboard.models import NewsItem, Quote
from dashboard.render import PageView, TileView, build_page, render_site
from dashboard.store import SourceStatus, append_quotes, write_meta, write_news

TODAY = date(2026, 8, 5)
REPO_ROOT = Path(__file__).resolve().parents[1]

BRENT = instrument(id="brent", name="Brent crude", group="crude", unit="USD/bbl")
NBP = instrument(
    id="nbp",
    name="UK NBP",
    group="gas",
    unit="EUR/MWh",
    currency="EUR",
    proxy="NBP is licensed by ICE. This is TTF.",
)


def config_for(*instruments: Instrument) -> Config:
    groups = tuple(
        dict.fromkeys(Group(id=i.group, name=i.group.title()) for i in instruments)
    )
    return Config(groups=groups, instruments=instruments)


def seed(
    data_dir: Path, instrument_id: str, points: list[tuple[int, float]], unit: str
) -> None:
    append_quotes(
        data_dir,
        [
            Quote(
                instrument=instrument_id,
                observed=date(2026, 8, day),
                value=value,
                unit=unit,
            )
            for day, value in points
        ],
    )


def tile_for(page: PageView, instrument_id: str) -> TileView:
    for group in page.groups:
        for tile in group.tiles:
            if tile.id == instrument_id:
                return tile
    raise AssertionError(f"no tile for {instrument_id}")


# ---- view model ----


def test_change_and_direction(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(4, 80.0), (5, 82.0)], "USD/bbl")
    tile = tile_for(build_page(config_for(BRENT), tmp_path, today=TODAY), "brent")

    assert tile.value == "82.00"
    assert tile.change == "+2.00"
    assert tile.change_pct == "+2.50%"
    assert tile.direction == "up"


def test_a_fall_reads_as_a_fall(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(4, 80.0), (5, 78.0)], "USD/bbl")
    tile = tile_for(build_page(config_for(BRENT), tmp_path, today=TODAY), "brent")
    assert tile.change == "-2.00"
    assert tile.direction == "down"


def test_a_single_point_has_no_change(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(5, 80.0)], "USD/bbl")
    tile = tile_for(build_page(config_for(BRENT), tmp_path, today=TODAY), "brent")
    assert tile.change is None
    assert tile.value == "80.00"


def test_an_instrument_with_no_data_renders_as_empty(tmp_path: Path) -> None:
    tile = tile_for(build_page(config_for(BRENT), tmp_path, today=TODAY), "brent")
    assert tile.value is None
    assert tile.has_value is False
    assert tile.observed_label == "no data yet"


def test_old_observations_are_marked_stale(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(1, 80.0)], "USD/bbl")
    tile = tile_for(
        build_page(config_for(BRENT), tmp_path, today=date(2026, 8, 20)), "brent"
    )
    assert tile.stale is True
    assert tile.age_days == 19


def test_a_weekend_gap_is_not_called_stale(tmp_path: Path) -> None:
    """Friday's close read on Monday is normal, not a failure worth badging."""
    seed(tmp_path, "brent", [(31, 80.0)], "USD/bbl")
    page = build_page(config_for(BRENT), tmp_path, today=date(2026, 8, 3))
    assert tile_for(page, "brent").stale is False


def test_sterling_conversion_uses_the_stored_rate(tmp_path: Path) -> None:
    gbpusd = instrument(
        id="gbpusd",
        source="fx",
        symbol="USD",
        unit="USD per GBP",
        group="fx",
        precision=4,
    )
    seed(tmp_path, "gbpusd", [(5, 1.25)], "USD per GBP")
    seed(tmp_path, "brent", [(5, 80.0)], "USD/bbl")

    page = build_page(config_for(BRENT, gbpusd), tmp_path, today=TODAY)
    assert tile_for(page, "brent").sterling == "£64.00"
    assert page.fx_note is not None


def test_no_sterling_line_without_a_rate(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(5, 80.0)], "USD/bbl")
    page = build_page(config_for(BRENT), tmp_path, today=TODAY)
    assert tile_for(page, "brent").sterling is None
    assert page.fx_note is None


def test_a_sterling_instrument_is_not_converted_to_itself(tmp_path: Path) -> None:
    gbp = instrument(id="uk_wholesale", group="power", unit="GBP/MWh", currency="GBP")
    gbpusd = instrument(
        id="gbpusd", source="fx", symbol="USD", group="fx", unit="USD per GBP"
    )
    seed(tmp_path, "gbpusd", [(5, 1.25)], "USD per GBP")
    seed(tmp_path, "uk_wholesale", [(5, 120.0)], "GBP/MWh")

    page = build_page(config_for(gbp, gbpusd), tmp_path, today=TODAY)
    assert tile_for(page, "uk_wholesale").sterling is None


def test_sparkline_needs_at_least_two_points(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(5, 80.0)], "USD/bbl")
    assert (
        tile_for(build_page(config_for(BRENT), tmp_path, today=TODAY), "brent").spark
        is None
    )


def test_a_flat_series_still_draws(tmp_path: Path) -> None:
    """A zero range must not divide by zero."""
    seed(tmp_path, "brent", [(3, 80.0), (4, 80.0), (5, 80.0)], "USD/bbl")
    spark = tile_for(build_page(config_for(BRENT), tmp_path, today=TODAY), "brent").spark
    assert spark is not None
    assert "14.00" in spark


def test_source_problems_are_surfaced(tmp_path: Path) -> None:
    write_meta(
        tmp_path,
        {"eia": SourceStatus("eia", last_success=None, last_error="no key")},
        generated=datetime(2026, 8, 5, tzinfo=UTC),
    )
    page = build_page(config_for(BRENT), tmp_path, today=TODAY)
    assert any("eia" in problem for problem in page.problems)


def test_a_healthy_source_is_not_reported(tmp_path: Path) -> None:
    write_meta(
        tmp_path,
        {"yahoo": SourceStatus("yahoo", last_success=datetime.now(tz=UTC))},
        generated=datetime.now(tz=UTC),
    )
    assert build_page(config_for(BRENT), tmp_path, today=TODAY).problems == ()


def test_a_corrupt_history_costs_one_tile_not_the_page(tmp_path: Path) -> None:
    seed(tmp_path, "nbp", [(5, 50.0)], "EUR/MWh")
    (tmp_path / "brent.json").write_text("{ not json", encoding="utf-8")

    page = build_page(config_for(BRENT, NBP), tmp_path, today=TODAY)
    assert tile_for(page, "brent").has_value is False
    assert tile_for(page, "nbp").has_value is True


# ---- rendered output ----


def render_html(tmp_path: Path, config: Config) -> str:
    site = tmp_path / "site"
    render_site(
        config,
        tmp_path,
        site,
        template_dir=REPO_ROOT / "templates",
        static_dir=REPO_ROOT / "static",
        today=TODAY,
    )
    return (site / "index.html").read_text(encoding="utf-8")


def test_a_proxy_always_renders_its_caveat(tmp_path: Path) -> None:
    """Rule 3, enforced on the output rather than trusted to the template."""
    seed(tmp_path, "nbp", [(5, 50.0)], "EUR/MWh")
    html = render_html(tmp_path, config_for(NBP))

    assert "NBP is licensed by ICE" in html
    assert "badge--proxy" in html


def test_a_proxy_caveat_survives_even_with_no_data(tmp_path: Path) -> None:
    """The tile with nothing in it is exactly where a caveat could get dropped."""
    html = render_html(tmp_path, config_for(NBP))
    assert "NBP is licensed by ICE" in html


def test_every_value_is_rendered_with_its_date(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(4, 80.0), (5, 82.0)], "USD/bbl")
    html = render_html(tmp_path, config_for(BRENT))
    assert "82.00" in html
    assert "as of today" in html


def test_the_disclaimer_is_on_the_page(tmp_path: Path) -> None:
    """Rule 6."""
    html = render_html(tmp_path, config_for(BRENT))
    assert "Nothing here is investment" in html


def test_news_is_rendered_with_its_publisher(tmp_path: Path) -> None:
    seed(tmp_path, "brent", [(5, 80.0)], "USD/bbl")
    write_news(
        tmp_path,
        {
            "brent": [
                NewsItem(
                    title="Brent settles higher",
                    url="https://example.com/a",
                    source="Reuters",
                    published=datetime(2026, 8, 5, tzinfo=UTC),
                )
            ]
        },
    )
    html = render_html(tmp_path, config_for(BRENT))
    assert "Brent settles higher" in html
    assert "Reuters" in html


def test_static_assets_are_copied(tmp_path: Path) -> None:
    site = tmp_path / "site"
    render_site(
        config_for(BRENT),
        tmp_path,
        site,
        template_dir=REPO_ROOT / "templates",
        static_dir=REPO_ROOT / "static",
        today=TODAY,
    )
    for name in (
        "style.css",
        "app.js",
        "sw.js",
        "manifest.webmanifest",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
    ):
        assert (site / name).exists(), name
    assert (site / ".nojekyll").exists()


def test_the_service_worker_gets_a_build_stamp(tmp_path: Path) -> None:
    """An unstamped worker would serve a stale shell forever."""
    site = tmp_path / "site"
    render_site(
        config_for(BRENT),
        tmp_path,
        site,
        template_dir=REPO_ROOT / "templates",
        static_dir=REPO_ROOT / "static",
        today=TODAY,
    )
    worker = (site / "sw.js").read_text(encoding="utf-8")
    assert "__BUILD_ID__" not in worker


def test_the_page_declares_itself_installable(tmp_path: Path) -> None:
    """The iOS home-screen path needs all of these; missing one breaks it quietly."""
    html = render_html(tmp_path, config_for(BRENT))
    for needle in (
        'rel="manifest"',
        'rel="apple-touch-icon"',
        "apple-mobile-web-app-capable",
        "viewport-fit=cover",
    ):
        assert needle in html, needle


def test_html_is_escaped(tmp_path: Path) -> None:
    hostile = instrument(id="brent", name="<script>alert(1)</script>", group="crude")
    seed(tmp_path, "brent", [(5, 80.0)], "USD/bbl")
    html = render_html(tmp_path, config_for(hostile))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


@pytest.mark.parametrize("missing", ["style.css", "app.js"])
def test_the_real_static_dir_has_what_the_template_asks_for(missing: str) -> None:
    assert (REPO_ROOT / "static" / missing).exists()
