import logging
import json
from datetime import datetime
from typing import Any

import httpx

from app.models import PolymarketMarket

logger = logging.getLogger(__name__)


class PolymarketClient:
    def __init__(self, events_url: str):
        self.events_url = events_url

    async def fetch_open_markets(self, limit: int = 50) -> list[PolymarketMarket]:
        params = {"closed": "false", "limit": limit}
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(self.events_url, params=params)
            resp.raise_for_status()
            payload = resp.json()

        if not isinstance(payload, list):
            logger.warning("Unexpected Polymarket payload type: %s", type(payload))
            return []

        markets: list[PolymarketMarket] = []
        for item in payload:
            markets.extend(self._extract_markets_from_event(item))
        return markets

    async def find_open_market_by_id(self, market_id: str, limit: int = 500) -> PolymarketMarket | None:
        markets = await self.fetch_open_markets(limit=limit)
        for market in markets:
            if market.market_id == market_id:
                return market
        return None

    def _extract_markets_from_event(self, event: dict[str, Any]) -> list[PolymarketMarket]:
        out: list[PolymarketMarket] = []
        markets = event.get("markets") or []
        for market in markets:
            question = str(market.get("question") or event.get("title") or "")
            if not question:
                continue
            try:
                yes_price = self._to_float(market.get("outcomePrices"), idx=0)
                no_price = self._to_float(market.get("outcomePrices"), idx=1)
            except Exception:
                yes_price = None
                no_price = None

            yes_token_id, no_token_id = self._extract_token_ids(market)

            end_date = None
            raw_end = market.get("endDate") or event.get("endDate")
            if raw_end:
                try:
                    end_date = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
                except ValueError:
                    end_date = None

            out.append(
                PolymarketMarket(
                    market_id=str(market.get("id") or market.get("slug") or ""),
                    question=question,
                    url=f"https://polymarket.com/event/{event.get('slug')}" if event.get("slug") else None,
                    volume_usd=float(market.get("volume") or 0),
                    liquidity_usd=float(market.get("liquidity") or 0),
                    yes_price=yes_price,
                    no_price=no_price,
                    yes_token_id=yes_token_id,
                    no_token_id=no_token_id,
                    end_date=end_date,
                )
            )
        return [m for m in out if m.market_id]

    @staticmethod
    def _extract_token_ids(market: dict[str, Any]) -> tuple[str | None, str | None]:
        raw = market.get("clobTokenIds")
        items: list[Any] = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, str):
            text = raw.strip()
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    items = parsed
            except Exception:
                parts = [p.strip() for p in text.strip("[]").split(",") if p.strip()]
                items = [p.strip('"') for p in parts]

        yes = str(items[0]) if len(items) > 0 and str(items[0]).strip() else None
        no = str(items[1]) if len(items) > 1 and str(items[1]).strip() else None
        return yes, no

    @staticmethod
    def _to_float(raw: Any, idx: int) -> float | None:
        if raw is None:
            return None
        if isinstance(raw, list) and len(raw) > idx:
            return float(raw[idx])
        if isinstance(raw, str):
            parts = [x.strip() for x in raw.strip("[]").split(",")]
            if len(parts) > idx:
                return float(parts[idx].strip('"'))
        return None
