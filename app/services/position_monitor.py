"""
Position Monitor — следит за открытыми позициями и принимает решение о продаже.

Запускается каждые 2 минуты. Анализирует P&L открытых позиций.
Если прибыль > порога или рынок закрывается — уведомляет/продаёт.
"""
import logging
from datetime import datetime, timezone

from app.config import Settings
from app.services.execution import ExecutionService
from app.services.notifier import TelegramNotifier
from app.services.polymarket import PolymarketClient
from app.services.runtime_config import RuntimeConfigService
from app.services.storage import Storage

logger = logging.getLogger(__name__)

# Порог прибыли для авто-продажи (30%)
_SELL_PROFIT_THRESHOLD = 0.30
# Порог стоп-лосса (40% убытка)
_STOP_LOSS_THRESHOLD = 0.40
# Закрыть позицию если до конца рынка < 10 минут и мы в прибыли
_CLOSE_BEFORE_MINUTES = 10


class PositionMonitor:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        polymarket_client: PolymarketClient,
        execution: ExecutionService,
        notifier: TelegramNotifier,
        runtime_config: RuntimeConfigService,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.polymarket_client = polymarket_client
        self.execution = execution
        self.notifier = notifier
        self.runtime_config = runtime_config

    async def run_once(self) -> dict:
        positions = await self.storage.get_open_positions()
        if not positions:
            return {"status": "no_open_positions"}

        runtime = await self.runtime_config.snapshot()
        checked = 0
        closed = 0

        for pos in positions:
            try:
                result = await self._check_position(pos, runtime)
                checked += 1
                if result.get("closed"):
                    closed += 1
            except Exception as exc:
                logger.exception(
                    "Ошибка мониторинга позиции id=%s: %s", pos.get("id"), exc
                )

        return {"status": "done", "checked": checked, "closed": closed}

    async def _check_position(self, pos: dict, runtime) -> dict:
        market_id = pos["market_id"]
        action = pos["action"]
        entry_price: float = pos["entry_price"]
        amount_usd: float = pos["amount_usd"]

        # Получить текущее состояние рынка
        market = await self.polymarket_client.find_open_market_by_id(market_id, limit=300)

        if market is None:
            # Рынок закрылся — считаем P&L по последней известной цене
            logger.info("Рынок %s закрыт, закрываем позицию id=%s", market_id, pos["id"])
            await self.storage.close_position(pos["id"], exit_price=None, close_reason="market_closed")
            return {"closed": True, "reason": "market_closed"}

        # Текущая цена нашей стороны
        current_price = market.yes_price if action == "BET_YES" else market.no_price
        if current_price is None:
            return {"closed": False, "reason": "price_unavailable"}

        # P&L
        if entry_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = 0.0

        logger.debug(
            "Позиция id=%s %s: entry=%.3f current=%.3f pnl=%.1f%%",
            pos["id"], action, entry_price, current_price, pnl_pct * 100,
        )

        # Проверить время до закрытия рынка
        close_soon = False
        if market.end_date:
            now = datetime.now(timezone.utc)
            minutes_left = (market.end_date - now).total_seconds() / 60
            if minutes_left < _CLOSE_BEFORE_MINUTES and pnl_pct > 0:
                close_soon = True
                logger.info(
                    "Рынок %s закрывается через %.1f мин, P&L=%.1f%% — сигнал к продаже.",
                    market_id, minutes_left, pnl_pct * 100,
                )

        should_sell = False
        sell_reason = ""

        if pnl_pct >= _SELL_PROFIT_THRESHOLD:
            should_sell = True
            sell_reason = f"Прибыль {pnl_pct:.1%} достигла порога {_SELL_PROFIT_THRESHOLD:.0%}"
        elif pnl_pct <= -_STOP_LOSS_THRESHOLD:
            should_sell = True
            sell_reason = f"Стоп-лосс: убыток {abs(pnl_pct):.1%} достиг порога {_STOP_LOSS_THRESHOLD:.0%}"
        elif close_soon:
            should_sell = True
            sell_reason = f"Рынок закрывается, фиксируем прибыль {pnl_pct:.1%}"

        if not should_sell:
            return {"closed": False, "pnl_pct": pnl_pct}

        # Продажа
        simulated = runtime.dry_run or not runtime.auto_execute
        if not simulated:
            sell_ok = await self._execute_sell(pos, market, current_price, runtime)
            simulated = not sell_ok

        await self.storage.close_position(pos["id"], exit_price=current_price, close_reason=sell_reason)

        await self.notifier.notify_position_action(
            position=pos,
            action=sell_reason,
            current_price=current_price,
            pnl_pct=pnl_pct * 100,
            simulated=simulated,
        )

        logger.info(
            "Позиция id=%s закрыта: %s | pnl=%.1f%% | simulated=%s",
            pos["id"], sell_reason, pnl_pct * 100, simulated,
        )
        return {"closed": True, "pnl_pct": pnl_pct, "reason": sell_reason}

    async def _execute_sell(self, pos: dict, market, current_price: float, runtime) -> bool:
        """Размещает ордер на продажу через CLOB. Возвращает True если успешно."""
        if not runtime.polymarket_private_key or not runtime.polymarket_funder_address:
            logger.warning("Продажа позиции: ключи Polymarket не настроены, пропускаем.")
            return False

        token_id = pos.get("token_id")
        if not token_id:
            logger.warning("Продажа позиции id=%s: нет token_id.", pos["id"])
            return False

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderType
            from py_clob_client.order_builder.constants import SELL

            client = ClobClient(
                runtime.polymarket_clob_host,
                key=runtime.polymarket_private_key,
                chain_id=runtime.polymarket_chain_id,
                signature_type=runtime.polymarket_signature_type,
                funder=runtime.polymarket_funder_address,
            )
            client.set_api_creds(client.create_or_derive_api_creds())

            from py_clob_client.clob_types import MarketOrderArgs
            sell_order = MarketOrderArgs(
                token_id=token_id,
                amount=float(pos["amount_usd"]),
                side=SELL,
                order_type=OrderType.FOK,
            )
            signed = client.create_market_order(sell_order)
            resp = client.post_order(signed, OrderType.FOK)
            logger.info("Продажа позиции id=%s: ответ CLOB: %s", pos["id"], resp)
            return True
        except Exception as exc:
            logger.exception("Ошибка продажи позиции id=%s: %s", pos["id"], exc)
            return False
