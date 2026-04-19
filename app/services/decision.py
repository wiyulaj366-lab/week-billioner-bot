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
            return Decision(rationale="No candidate markets found for this event.")

        market = analysis.packet.candidate_markets[0]
        if market.volume_usd < runtime.min_market_volume:
            return Decision(
                market=market,
                action="SKIP",
                confidence=analysis.consensus_confidence,
                rationale="Market volume below threshold.",
                blocked_by_guardrail=True,
                guardrail_reason="min_market_volume",
            )

        daily_pnl = await self.storage.daily_pnl()
        if daily_pnl <= -abs(runtime.max_daily_loss_usd):
            return Decision(
                market=market,
                action="SKIP",
                confidence=analysis.consensus_confidence,
                rationale="Daily loss limit reached.",
                blocked_by_guardrail=True,
                guardrail_reason="max_daily_loss",
            )

        if analysis.consensus_confidence < runtime.min_confidence:
            return Decision(
                market=market,
                action="SKIP",
                confidence=analysis.consensus_confidence,
                rationale="Consensus confidence below threshold.",
                blocked_by_guardrail=True,
                guardrail_reason="min_confidence",
            )

        if analysis.consensus_side == "SKIP":
            return Decision(
                market=market,
                action="SKIP",
                confidence=analysis.consensus_confidence,
                rationale="Models consensus is SKIP.",
            )

        action = "BET_YES" if analysis.consensus_side == "YES" else "BET_NO"
        stake = min(runtime.max_bet_usd, round(runtime.max_bet_usd * analysis.consensus_confidence, 2))
        return Decision(
            market=market,
            action=action,
            stake_usd=max(stake, 1.0),
            confidence=analysis.consensus_confidence,
            rationale=analysis.summary_reasoning[:1000],
        )
