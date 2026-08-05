"""Per-instrument news headlines from RSS.

The feed URL is built from the instrument's ``news`` term, so adding a feed to a
commodity is a config edit. Headlines are fetched through the project's `httpx`
client rather than letting feedparser do its own networking, so timeouts and the
user-agent stay consistent with every other request.

News failures are cosmetic: a tile without headlines is fine, a tile with a wrong
price is not. Every failure here degrades to an empty list.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote_plus

import feedparser
import httpx

from dashboard.models import NewsItem
from dashboard.sources.base import build_client

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from dashboard.config import Instrument

log = logging.getLogger(__name__)

#: Google News RSS search, scoped to UK English.
FEED_URL: Final = "https://news.google.com/rss/search?q={query}&hl=en-GB&gl=GB&ceid=GB:en"

#: Headlines kept per instrument. The page is a dashboard, not a reader.
MAX_ITEMS: Final = 4


class NewsSource:
    """Headlines per instrument. Never raises; an empty list is an acceptable answer."""

    id = "news"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def fetch(
        self, instruments: Sequence[Instrument], *, limit: int = MAX_ITEMS
    ) -> dict[str, list[NewsItem]]:
        """Headlines keyed by instrument id; instruments without a term are skipped."""
        headlines: dict[str, list[NewsItem]] = {}
        client = self._client or build_client()
        try:
            for instrument in instruments:
                if not instrument.news:
                    continue
                items = self._fetch_one(client, instrument.news, limit=limit)
                if items:
                    headlines[instrument.id] = items
        finally:
            if self._client is None:
                client.close()
        return headlines

    def _fetch_one(
        self, client: httpx.Client, query: str, *, limit: int
    ) -> list[NewsItem]:
        url = FEED_URL.format(query=quote_plus(query))
        try:
            response = client.get(url, headers={"Accept": "application/rss+xml"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("news: %r unavailable: %s", query, exc)
            return []
        return parse_feed(response.content, limit=limit)


def parse_feed(payload: bytes | str, *, limit: int = MAX_ITEMS) -> list[NewsItem]:
    """Parse an RSS body into news items, newest first."""
    parsed = feedparser.parse(payload)
    items: list[NewsItem] = []
    for entry in parsed.entries[: limit * 2]:
        title = _text(entry, "title")
        link = _text(entry, "link")
        if not title or not link:
            continue
        items.append(
            NewsItem(
                title=title,
                url=link,
                source=_publisher(entry),
                published=_published(entry),
            )
        )
        if len(items) >= limit:
            break
    return items


def _text(entry: Mapping[str, Any], key: str) -> str:
    value = entry.get(key)
    return value.strip() if isinstance(value, str) else ""


def _publisher(entry: Mapping[str, Any]) -> str:
    """Google News nests the originating publication under ``source``."""
    source = entry.get("source")
    if isinstance(source, dict):
        title = source.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return "Google News"


def _published(entry: Mapping[str, Any]) -> datetime | None:
    """Timezone-aware publication time, or ``None`` when the feed did not give one."""
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    try:
        year, month, day, hour, minute, second = parsed[:6]
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except (TypeError, ValueError):
        return None
