from app.config import Settings
from app.models import AggregatedAnalysis, Decision
from app.services.runtime_config import RuntimeConfigService
from app.services.storage import Storage


class DecisionService:
    def __init__(self, settings: Settings, storage: Storage, runtime_config: RuntimeConfigService):
        self.settings = settings
        self.storage = storage
        self.runtime_config = runtime_config

    async def decide(self, analysis: AggregatedAnalysis) -> Decision:
        runtime = await self.runtime_config.snapshot()
        if not analysis.packet.candidate_markets:
            return Decision(action="NO_BET", rationale="Подходящих рынков для события не найдено.")

        if analysis.packet.priority_score < 1.0:
            return Decision(
                action="NO_BET",
                confidence=analysis.consensus_confidence,
                rationale="Событие имеет низкий приоритет влияния на крипто-рынки.",
                blocked_by_guardrail=True,
                guardrail_reason="low_priority",
            )

        market = analysis.packet.candidate_markets[0]
        if market.volume_usd < runtime.min_market_volume:
            return Decision(
                market=market,
                action="NO_BET",
                confidence=analysis.consensus_confidence,
                rationale="Объем рынка ниже минимального порога.",
                blocked_by_guardrail=True,
                guardrail_reason="min_market_volume",
            )

        daily_pnl = await self.storage.daily_pnl()
        if daily_pnl <= -abs(runtime.max_daily_loss_usd):
            return Decision(
                market=market,
                action="NO_BET",
                confidence=analysis.consensus_confidence,
                rationale="Достигнут дневной лимит убытка.",
                blocked_by_guardrail=True,
                guardrail_reason="max_daily_loss",
            )

        if analysis.consensus_confidence < runtime.min_confidence:
            return Decision(
                market=market,
                action="NO_BET",
                confidence=analysis.consensus_confidence,
                rationale="Уверенность консенсуса ниже заданного порога.",
                blocked_by_guardrail=True,
                guardrail_reason="min_confidence",
            )

        action = "BET_YES" if analysis.consensus_side == "YES" else "BET_NO"
        is_btc_market = "bitcoin" in market.question.lower() or "btc" in market.question.lower()
        stake_target = runtime.max_bet_usd * analysis.consensus_confidence
        if is_btc_market:
            stake_target *= 1.2
        stake = min(runtime.max_bet_usd, round(stake_target, 2))
        rationale = analysis.summary_reasoning[:1000]
        if is_btc_market:
            rationale = "[GOLDEN_BTC] " + rationale
        return Decision(
            market=market,
            action=action,
            stake_usd=max(stake, 1.0),
            confidence=analysis.consensus_confidence,
            rationale=rationale,
        )
