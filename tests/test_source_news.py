"""News: headlines are cosmetic, so every failure degrades to an empty list."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from conftest import error_client, fixture_text, instrument, mock_client
from dashboard.sources.news import NewsSource, parse_feed


def test_parses_google_news_rss() -> None:
    items = parse_feed(fixture_text("google_news.xml"))
    assert len(items) == 3
    assert items[0].title == "Brent settles higher as OPEC+ holds output steady"
    assert items[0].url.startswith("https://news.google.com/")


def test_publisher_comes_from_the_nested_source_element() -> None:
    items = parse_feed(fixture_text("google_news.xml"))
    assert [i.source for i in items] == ["Reuters", "Argus Media", "Bloomberg"]


def test_published_times_are_timezone_aware() -> None:
    items = parse_feed(fixture_text("google_news.xml"))
    assert items[0].published == datetime(2026, 8, 4, 17, 42, tzinfo=UTC)


def test_limit_is_respected() -> None:
    assert len(parse_feed(fixture_text("google_news.xml"), limit=2)) == 2


def test_junk_input_yields_no_items() -> None:
    assert parse_feed("not xml at all") == []


def test_entries_without_a_link_are_dropped() -> None:
    feed = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>Headline with no link</title></item>
    </channel></rss>"""
    assert parse_feed(feed) == []


def test_fetch_keys_headlines_by_instrument() -> None:
    body = fixture_text("google_news.xml").encode()
    client = mock_client(lambda _r: httpx.Response(200, content=body))
    headlines = NewsSource(client=client).fetch([instrument(news="Brent crude")])
    assert list(headlines) == ["brent"]
    assert len(headlines["brent"]) == 3


def test_instruments_without_a_term_are_skipped() -> None:
    body = fixture_text("google_news.xml").encode()
    client = mock_client(lambda _r: httpx.Response(200, content=body))
    assert NewsSource(client=client).fetch([instrument(news=None)]) == {}


def test_a_failing_feed_is_not_an_error() -> None:
    """A tile without headlines is fine. A run that died fetching them is not."""
    source = NewsSource(client=error_client(503))
    assert source.fetch([instrument(news="Brent crude")]) == {}
