import re

from app.models import EventPacket, PolymarketMarket, WorldEvent
from app.services.polymarket import PolymarketClient
from app.services.storage import Storage
from app.services.world_events import WorldEventsClient


class IngestionService:
    def __init__(self, world_client: WorldEventsClient, polymarket_client: PolymarketClient, storage: Storage):
        self.world_client = world_client
        self.polymarket_client = polymarket_client
        self.storage = storage

    async def collect_event_packets(self, max_events: int) -> list[EventPacket]:
        world_events = await self.world_client.fetch_latest()
        markets = await self.polymarket_client.fetch_open_markets(limit=100)

        packets: list[EventPacket] = []
        for event in world_events:
            if await self.storage.is_processed(event.url):
                continue
            matched = self._match_markets(event, markets)
            if matched:
                packets.append(EventPacket(world_event=event, candidate_markets=matched[:3]))
            if len(packets) >= max_events:
                break
        return packets

    def _match_markets(self, event: WorldEvent, markets: list[PolymarketMarket]) -> list[PolymarketMarket]:
        text = f"{event.title} {event.summary}".lower()
        event_tokens = self._keywords(text)
        if not event_tokens:
            return []

        scored: list[tuple[int, PolymarketMarket]] = []
        for market in markets:
            market_tokens = self._keywords(market.question.lower())
            overlap = len(event_tokens.intersection(market_tokens))
            if overlap > 0:
                scored.append((overlap, market))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [market for _, market in scored]

    @staticmethod
    def _keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z]{4,}", text)
        stopwords = {
            "will",
            "that",
            "with",
            "from",
            "this",
            "have",
            "they",
            "their",
            "about",
            "after",
            "would",
            "there",
            "which",
            "today",
            "state",
            "world",
            "could",
            "than",
        }
        return {t for t in tokens if t not in stopwords}
