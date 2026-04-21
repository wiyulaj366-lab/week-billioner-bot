import re

from app.models import EventPacket, PolymarketMarket, WorldEvent
from app.services.polymarket import PolymarketClient
from app.services.storage import Storage
from app.services.world_events import WorldEventsClient

CRYPTO_KEYWORDS = {
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "crypto",
    "cryptocurrency",
    "stablecoin",
    "binance",
    "sec",
    "etf",
    "token",
    "blockchain",
    "defi",
    "coinbase",
    "altcoin",
}

MACRO_RISK_KEYWORDS = {
    "war",
    "ukraine",
    "russia",
    "israel",
    "iran",
    "gaza",
    "nato",
    "sanctions",
    "oil",
    "inflation",
    "fed",
    "rate",
    "recession",
    "conflict",
    "military",
    "missile",
}


class IngestionService:
    def __init__(self, world_client: WorldEventsClient, polymarket_client: PolymarketClient, storage: Storage):
        self.world_client = world_client
        self.polymarket_client = polymarket_client
        self.storage = storage

    async def collect_event_packets(self, max_events: int) -> list[EventPacket]:
        world_events = await self.world_client.fetch_latest(per_feed_limit=20)
        markets = await self.polymarket_client.fetch_open_markets(limit=150)

        packets: list[EventPacket] = []
        for market in markets:
            if not self._is_crypto_market(market):
                continue

            event, relevance = self._best_related_news(market, world_events)
            if event is None:
                continue

            tracking_url = self._tracking_url(event.url, market.market_id)
            if await self.storage.is_processed(tracking_url):
                continue

            tracked_event = event.model_copy(update={"url": tracking_url})
            score, reason = self._priority_score(tracked_event, market)
            score += relevance
            packets.append(
                EventPacket(
                    world_event=tracked_event,
                    candidate_markets=[market],
                    priority_score=score,
                    priority_reason=reason,
                )
            )

        packets.sort(key=lambda p: p.priority_score, reverse=True)
        return packets[:max_events]

    def _best_related_news(
        self,
        market: PolymarketMarket,
        world_events: list[WorldEvent],
    ) -> tuple[WorldEvent | None, float]:
        market_tokens = self._keywords(market.question.lower())
        if not market_tokens:
            return None, 0.0

        best_event: WorldEvent | None = None
        best_score = 0.0
        is_btc_market = self._is_bitcoin_text(market.question.lower())
        for event in world_events:
            text = f"{event.title} {event.summary}".lower()
            event_tokens = self._keywords(text)
            overlap = len(event_tokens.intersection(market_tokens))
            if overlap <= 0:
                continue
            score = float(overlap)
            if is_btc_market and self._is_bitcoin_text(text):
                score += 4.0
            if score > best_score:
                best_score = score
                best_event = event

        return best_event, best_score

    def _match_markets(self, event: WorldEvent, markets: list[PolymarketMarket]) -> list[PolymarketMarket]:
        text = f"{event.title} {event.summary}".lower()
        event_tokens = self._keywords(text)
        if not event_tokens:
            return []

        scored: list[tuple[float, PolymarketMarket]] = []
        event_is_btc = self._is_bitcoin_text(text)
        for market in markets:
            market_tokens = self._keywords(market.question.lower())
            overlap = len(event_tokens.intersection(market_tokens))
            if overlap <= 0:
                continue
            market_boost = min(market.volume_usd / 10000.0, 3.0) + min(market.liquidity_usd / 10000.0, 2.0)
            btc_boost = 0.0
            if event_is_btc and self._is_bitcoin_text(market.question.lower()):
                btc_boost = 4.0
            scored.append((overlap + market_boost + btc_boost, market))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [market for _, market in scored]

    def _priority_score(self, event: WorldEvent, top_market: PolymarketMarket) -> tuple[float, str]:
        text = f"{event.title} {event.summary}".lower()
        tokens = self._keywords(text)
        crypto_hits = len(tokens.intersection(CRYPTO_KEYWORDS))
        macro_hits = len(tokens.intersection(MACRO_RISK_KEYWORDS))
        is_btc_event = self._is_bitcoin_text(text)
        is_btc_market = self._is_bitcoin_text(top_market.question.lower())

        score = 0.0
        score += crypto_hits * 1.8
        score += macro_hits * 0.9
        score += min(top_market.volume_usd / 15000.0, 2.5)
        score += min(top_market.liquidity_usd / 15000.0, 1.5)
        if is_btc_event:
            score += 2.5
        if is_btc_event and is_btc_market:
            score += 5.0

        if is_btc_event and is_btc_market:
            reason = "GOLDEN BTC: релевантная новость + рынок Bitcoin"
        elif crypto_hits > 0:
            reason = "Прямой крипто-триггер"
        elif macro_hits > 0:
            reason = "Макро/геополитический риск для крипто"
        else:
            reason = "Базовое совпадение с рынком"
        return score, reason

    @staticmethod
    def _is_bitcoin_text(text: str) -> bool:
        return "bitcoin" in text or "btc" in text

    @staticmethod
    def _is_crypto_market(market: PolymarketMarket) -> bool:
        q = market.question.lower()
        return any(k in q for k in CRYPTO_KEYWORDS)

    @staticmethod
    def _tracking_url(source_url: str, market_id: str) -> str:
        sep = "&" if "?" in source_url else "?"
        return f"{source_url}{sep}pm_market={market_id}"

    @staticmethod
    def _keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z]{3,}", text)
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
            "said",
            "says",
            "into",
            "over",
        }
        return {t for t in tokens if t not in stopwords}
