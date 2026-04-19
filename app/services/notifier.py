import logging

import httpx

from app.config import Settings
from app.models import AggregatedAnalysis, Decision, ExecutionResult

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def notify(
        self,
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
    ) -> None:
        if not self.settings.telegram_enabled():
            return
        text = self._build_message(analysis, decision, execution)
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as exc:
            logger.exception("Telegram notify failed: %s", exc)

    def _build_message(
        self,
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
    ) -> str:
        event = analysis.packet.world_event
        market = decision.market.question if decision.market else "N/A"
        lines = [
            "week-billioner-bot alert",
            f"Event: {event.title}",
            f"Source: {event.source}",
            f"URL: {event.url}",
            f"Market: {market}",
            f"Action: {decision.action}",
            f"Stake USD: {decision.stake_usd:.2f}",
            f"Confidence: {decision.confidence:.2f}",
            f"Reasoning: {decision.rationale}",
            f"Execution: {execution.message}",
            "",
            "Model views:",
        ]
        for model in analysis.model_outputs:
            lines.append(
                f"- {model.model_name}: side={model.recommended_side}, "
                f"conf={model.confidence:.2f}, shift={model.probability_shift:.2f}, "
                f"thesis={model.thesis}"
            )
        return "\n".join(lines)[:3900]
