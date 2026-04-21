"""
BTC Price Ticker — источник цены для BTC 5-мин пайплайна.
Primary: Chainlink Data Streams BTC/USD (public delayed page).
Fallback/candles: Binance REST API.
"""
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CHAINLINK_STREAM_URL = "https://data.chain.link/streams/btc-usd"
_BINANCE_BASE = "https://api.binance.com/api/v3"
_SYMBOL = "BTCUSDT"


class BtcTicker:
    """Получает цену BTC с приоритетом Chainlink и свечи с Binance."""

    async def get_price_with_source(self) -> dict[str, Any]:
        """
        Возвращает цену с указанием источника.

        Формат:
        {
          "price": float,
          "source": "Chainlink Data Streams BTC/USD (delayed public page)",
          "source_url": "https://data.chain.link/streams/btc-usd"
        }
        """
        try:
            price = await self._get_chainlink_price_from_public_page()
            return {
                "price": price,
                "source": "Chainlink Data Streams BTC/USD (delayed public page)",
                "source_url": _CHAINLINK_STREAM_URL,
            }
        except Exception as exc:
            logger.warning("Chainlink price fetch failed, fallback to Binance: %s", exc)
            price = await self._get_binance_price()
            return {
                "price": price,
                "source": "Binance BTCUSDT (fallback)",
                "source_url": f"{_BINANCE_BASE}/ticker/price?symbol={_SYMBOL}",
            }

    async def get_price(self) -> float:
        """Возвращает текущую цену BTC (Chainlink-first)."""
        data = await self.get_price_with_source()
        return float(data["price"])

    async def _get_binance_price(self) -> float:
        """Возвращает текущую цену BTC/USDT с Binance."""
        url = f"{_BINANCE_BASE}/ticker/price"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"symbol": _SYMBOL})
            resp.raise_for_status()
            return float(resp.json()["price"])

    async def _get_chainlink_price_from_public_page(self) -> float:
        """
        Возвращает цену BTC/USD, распарсив публичную страницу Chainlink Streams.
        Важно: публичная страница может быть delayed.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_CHAINLINK_STREAM_URL)
            resp.raise_for_status()
            text = resp.text

        # Ищем денежные значения вида $75,608.18 и берем первое валидное.
        candidates = re.findall(r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
        for raw in candidates:
            try:
                value = float(raw.replace(",", ""))
                if value > 1000:  # фильтр от нерелевантных мелких чисел
                    return value
            except ValueError:
                continue

        raise ValueError("Не удалось извлечь цену BTC из страницы Chainlink")

    async def get_candles(self, interval: str = "1m", limit: int = 15) -> list[dict[str, Any]]:
        """
        Возвращает последние N свечей для BTCUSDT (Binance).
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
