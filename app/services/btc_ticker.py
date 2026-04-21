"""
BTC Price Ticker — источник цен Binance REST API.
Используется BTC 5-мин пайплайном для получения актуальных котировок.
"""
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BINANCE_BASE = "https://api.binance.com/api/v3"
_SYMBOL = "BTCUSDT"


class BtcTicker:
    """Получает текущую цену BTC и последние свечи с Binance."""

    async def get_price(self) -> float:
        """Возвращает текущую цену BTC/USDT."""
        url = f"{_BINANCE_BASE}/ticker/price"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"symbol": _SYMBOL})
            resp.raise_for_status()
            return float(resp.json()["price"])

    async def get_candles(self, interval: str = "1m", limit: int = 15) -> list[dict[str, Any]]:
        """
        Возвращает последние N свечей для BTCUSDT.
        Каждая свеча: {time, open, high, low, close, volume}
        """
        url = f"{_BINANCE_BASE}/klines"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={
                "symbol": _SYMBOL,
                "interval": interval,
                "limit": limit,
            })
            resp.raise_for_status()
            raw = resp.json()

        result = []
        for k in raw:
            result.append({
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return result

    async def get_summary(self) -> dict[str, Any]:
        """
        Быстрый запрос 24h статистики: цена, изменение за сутки, объём.
        """
        url = f"{_BINANCE_BASE}/ticker/24hr"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"symbol": _SYMBOL})
            resp.raise_for_status()
            d = resp.json()
        return {
            "price": float(d["lastPrice"]),
            "change_pct_24h": float(d["priceChangePercent"]),
            "high_24h": float(d["highPrice"]),
            "low_24h": float(d["lowPrice"]),
            "volume_24h_usdt": float(d["quoteVolume"]),
        }
