from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import feedparser

from app.models import WorldEvent


class WorldEventsClient:
    def __init__(self, feeds: Iterable[str]):
        self.feeds = list(feeds)

    async def fetch_latest(self, per_feed_limit: int = 5) -> list[WorldEvent]:
        events: list[WorldEvent] = []
        for feed_url in self.feeds:
            parsed = feedparser.parse(feed_url)
            source = parsed.feed.get("title", feed_url)
            for item in parsed.entries[:per_feed_limit]:
                events.append(
                    WorldEvent(
                        source=str(source),
                        title=str(item.get("title", "")).strip(),
                        summary=str(item.get("summary", "")).strip(),
                        url=str(item.get("link", "")).strip(),
                        published_at=self._parse_published(item.get("published")),
                    )
                )
        return [e for e in events if e.title and e.url]

    @staticmethod
    def _parse_published(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
