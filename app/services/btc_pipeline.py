"""
BTC 5-мин Pipeline — анализирует краткосрочное движение Bitcoin
и делает ставки на Polymarket BTC Up/Down рынки.

Запускается каждые 4 минуты (чуть меньше 5-мин окна).
"""
import json
import logging
from datetime import datetime, timezone

import httpx

from app.config import LLMConfig, Settings
from app.models import Decision, ExecutionResult, ModelAnalysis, PolymarketMarket
from app.prompts import SYSTEM_PROMPT_BTC, make_btc_prompt
from app.services.btc_ticker import BtcTicker
from app.services.execution import ExecutionService
from app.services.notifier import TelegramNotifier
from app.services.polymarket import PolymarketClient
from app.services.runtime_config import RuntimeConfigService
from app.services.storage import Storage
from app.services.world_events import WorldEventsClient

logger = logging.getLogger(__name__)

_BTC_MIN_CONFIDENCE = 0.60   # минимальная уверенность для сигнала
_BTC_BASE_STAKE_USD = 1.0    # базовая ставка ($1), масштабируется по confidence


class BtcPipeline:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        ticker: BtcTicker,
        world_client: WorldEventsClient,
        polymarket_client: PolymarketClient,
        execution: ExecutionService,
        notifier: TelegramNotifier,
        runtime_config: RuntimeConfigService,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.ticker = ticker
        self.world_client = world_client
        self.polymarket_client = polymarket_client
        self.execution = execution
        self.notifier = notifier
        self.runtime_config = runtime_config
        self._last_market_id: str | None = None  # дедуп ставок на один рынок

    async def run_once(self) -> dict:
        runtime = await self.runtime_config.snapshot()
        llms = runtime.llms
        if not llms:
            logger.warning("BTC pipeline: LLM не настроены, пропускаем цикл.")
            return {"status": "no_llm"}

        # 1. Найти активный BTC 5-мин рынок
        market = await self.polymarket_client.find_btc_updown_5min_market()
        if market is None:
            logger.info("BTC pipeline: активный BTC updown рынок не найден.")
            return {"status": "no_market"}

        # Дедуп: не ставить дважды на один рынок
        if market.market_id == self._last_market_id:
            logger.debug("BTC pipeline: уже делали ставку на рынок %s, ждём нового.", market.market_id)
            return {"status": "already_processed", "market_id": market.market_id}

        # 2. Получить цену BTC и свечи
        try:
            current_price = await self.ticker.get_price()
            candles = await self.ticker.get_candles(interval="1m", limit=15)
        except Exception as exc:
            logger.error("BTC pipeline: ошибка получения цены с Binance: %s", exc)
            return {"status": "binance_error", "error": str(exc)}

        # 3. Получить последние новости
        try:
            news = await self.world_client.fetch_latest(per_feed_limit=3)
            headlines = [e.title for e in news[:8]]
        except Exception as exc:
            logger.warning("BTC pipeline: ошибка загрузки новостей: %s", exc)
            headlines = []

        # 4. Запросить LLM
        user_prompt = make_btc_prompt(
            current_price=current_price,
            candles=candles,
            news_headlines=headlines,
            market_question=market.question,
            yes_price=market.yes_price,
            no_price=market.no_price,
        )

        analyses: list[ModelAnalysis] = []
        for llm in llms:
            analysis = await self._analyze_with_llm(llm, user_prompt, market.question)
            analyses.append(analysis)
            logger.info(
                "BTC LLM %s: side=%s conf=%.2f thesis=%s",
                llm.name, analysis.recommended_side, analysis.confidence, analysis.thesis[:100],
            )

        if not analyses:
            return {"status": "no_analysis"}

        # 5. Консенсус
        yes_votes = sum(1 for a in analyses if a.recommended_side == "YES")
        no_votes = sum(1 for a in analyses if a.recommended_side == "NO")
        consensus_side = "YES" if yes_votes > no_votes else "NO"
        consensus_conf = sum(a.confidence for a in analyses) / len(analyses)
        rationale = " | ".join(f"{a.model_name}: {a.thesis[:120]}" for a in analyses)

        logger.info(
            "BTC consensus: %s conf=%.2f (yes=%d no=%d) market=%s",
            consensus_side, consensus_conf, yes_votes, no_votes, market.market_id,
        )

        if consensus_conf < _BTC_MIN_CONFIDENCE:
            logger.debug("BTC pipeline: уверенность %.2f < %.2f, NO_BET.", consensus_conf, _BTC_MIN_CONFIDENCE)
            return {
                "status": "no_bet",
                "confidence": consensus_conf,
                "market_id": market.market_id,
            }

        # 6. Размер ставки: $1 * confidence (от $1 до max_bet)
        stake = round(
            min(
                runtime.max_bet_usd,
                max(_BTC_BASE_STAKE_USD, _BTC_BASE_STAKE_USD * consensus_conf * 2),
            ),
            2,
        )

        action = "BET_YES" if consensus_side == "YES" else "BET_NO"
        decision = Decision(
            market=market,
            action=action,
            stake_usd=stake,
            confidence=consensus_conf,
            rationale=f"[BTC_5MIN] {rationale}",
        )

        require_confirmation = not runtime.auto_execute

        if require_confirmation:
            execution = ExecutionResult(
                simulated=True,
                success=True,
                message="Ожидает подтверждения в админ-панели.",
            )
            decision_state = "pending_approval"
        else:
            execution = await self.execution.execute(
                decision,
                market_id=market.market_id,
            )
            decision_state = "executed"

        # Сохранить решение (храним как отдельную запись)
        decision_id = await self.storage.store_btc_decision(
            market=market,
            decision=decision,
            execution=execution,
            decision_state=decision_state,
            current_price=current_price,
            analyses=analyses,
        )

        # Если реально исполнено — трекаем позицию для мониторинга
        if decision_state == "executed" and execution.success:
            entry_price = market.yes_price if action == "BET_YES" else market.no_price
            await self.storage.add_open_position(
                decision_id=decision_id,
                market_id=market.market_id,
                token_id=(market.yes_token_id if action == "BET_YES" else market.no_token_id) or "",
                action=action,
                amount_usd=stake,
                entry_price=entry_price or 0.5,
                market_question=market.question,
                market_url=market.url or "",
            )

        # 7. Уведомить
        await self.notifier.notify_btc_signal(
            direction=consensus_side,
            confidence=consensus_conf,
            stake_usd=stake,
            rationale=rationale[:400],
            market_url=market.url or "",
            require_confirmation=require_confirmation,
            decision_id=decision_id,
        )

        self._last_market_id = market.market_id
        return {
            "status": "signaled",
            "action": action,
            "confidence": consensus_conf,
            "stake_usd": stake,
            "market_id": market.market_id,
            "decision_id": decision_id,
            "require_confirmation": require_confirmation,
        }

    async def _analyze_with_llm(
        self, llm: LLMConfig, user_prompt: str, market_question: str
    ) -> ModelAnalysis:
        payload = {
            "model": llm.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_BTC},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.15,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {llm.api_key}"}
        url = llm.base_url.rstrip("/") + "/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            side_raw = str(parsed.get("recommended_side", "NO")).upper()
            side = side_raw if side_raw in {"YES", "NO"} else "NO"
            return ModelAnalysis(
                model_name=llm.name,
                thesis=str(parsed.get("thesis", ""))[:400],
                probability_shift=0.0,
                confidence=float(parsed.get("confidence", 0.0)),
                risks=[str(x) for x in parsed.get("risks", [])][:5],
                recommended_side=side,
                time_horizon_hours=0,
            )
        except httpx.TimeoutException as exc:
            logger.error("BTC LLM таймаут %s: %s", llm.name, exc)
            return ModelAnalysis(
                model_name=llm.name,
                thesis=f"Таймаут запроса к {llm.name}",
                probability_shift=0.0,
                confidence=0.0,
                risks=["таймаут_модели"],
                recommended_side="NO",
                time_horizon_hours=0,
            )
        except Exception as exc:
            logger.exception("BTC LLM ошибка %s: %s", llm.name, exc)
            return ModelAnalysis(
                model_name=llm.name,
                thesis=f"Ошибка: {type(exc).__name__}: {exc}",
                probability_shift=0.0,
                confidence=0.0,
                risks=["ошибка_модели"],
                recommended_side="NO",
                time_horizon_hours=0,
            )
