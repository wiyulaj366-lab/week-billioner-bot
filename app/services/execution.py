from datetime import datetime, timezone

from app.config import Settings
from app.models import Decision, ExecutionResult
from app.services.polymarket import PolymarketClient
from app.services.runtime_config import RuntimeConfigService


class ExecutionService:
    def __init__(
        self,
        settings: Settings,
        runtime_config: RuntimeConfigService,
        polymarket_client: PolymarketClient,
    ):
        self.settings = settings
        self.runtime_config = runtime_config
        self.polymarket_client = polymarket_client

    async def execute(self, decision: Decision, market_id: str | None = None) -> ExecutionResult:
        runtime = await self.runtime_config.snapshot()
        if decision.action == "SKIP":
            return ExecutionResult(simulated=True, success=True, message="Информ-режим: SKIP.")

        target_market_id = market_id or (decision.market.market_id if decision.market else None)
        if not target_market_id:
            return ExecutionResult(simulated=True, success=False, message="Не указан market_id для исполнения.")

        market = await self.polymarket_client.find_open_market_by_id(target_market_id)
        if market is None:
            return ExecutionResult(
                simulated=True,
                success=False,
                message="Ставка отклонена: рынок уже закрыт или больше неактуален.",
            )

        if market.end_date and market.end_date <= datetime.now(timezone.utc):
            return ExecutionResult(
                simulated=True,
                success=False,
                message="Ставка отклонена: рынок завершен по времени.",
            )

        token_id = market.yes_token_id if decision.action == "BET_YES" else market.no_token_id
        if not token_id:
            return ExecutionResult(
                simulated=True,
                success=False,
                message="Ставка отклонена: не найден token_id для выбранной стороны.",
            )

        if runtime.dry_run:
            return ExecutionResult(
                simulated=True,
                success=True,
                order_id="simulated-order",
                message=(
                    f"Проверка актуальности пройдена. DRY_RUN: была бы выполнена "
                    f"{decision.action} на ${decision.stake_usd:.2f}."
                ),
            )

        if not runtime.polymarket_private_key or not runtime.polymarket_funder_address:
            return ExecutionResult(
                simulated=True,
                success=False,
                message=(
                    "Ставка отклонена: не настроены ключ Polymarket или funder address. "
                    "Заполни их в админ-боте."
                ),
            )

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY
        except Exception:
            return ExecutionResult(
                simulated=True,
                success=False,
                message="Ставка отклонена: пакет py-clob-client не установлен.",
            )

        try:
            client = ClobClient(
                runtime.polymarket_clob_host,
                key=runtime.polymarket_private_key,
                chain_id=runtime.polymarket_chain_id,
                signature_type=runtime.polymarket_signature_type,
                funder=runtime.polymarket_funder_address,
            )
            client.set_api_creds(client.create_or_derive_api_creds())

            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=float(decision.stake_usd),
                side=BUY,
                order_type=OrderType.FOK,
            )
            signed = client.create_market_order(market_order)
            response = client.post_order(signed, OrderType.FOK)

            order_id = None
            if isinstance(response, dict):
                order_id = str(response.get("orderID") or response.get("id") or "") or None
            return ExecutionResult(
                simulated=False,
                success=True,
                order_id=order_id,
                message=f"Ставка отправлена в Polymarket. Ответ: {response}",
            )
        except Exception as exc:
            return ExecutionResult(
                simulated=False,
                success=False,
                message=f"Ставка отклонена биржей/клиентом: {type(exc).__name__}: {exc}",
            )

        # Kept for structural completeness.
        return ExecutionResult(
            simulated=True,
            success=False,
            message="Не удалось выполнить ставку.",
        )
