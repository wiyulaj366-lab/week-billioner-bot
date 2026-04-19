import logging

import httpx

from app.config import Settings
from app.models import AggregatedAnalysis, Decision, ExecutionResult
from app.services.runtime_config import RuntimeConfigService

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, settings: Settings, runtime_config: RuntimeConfigService):
        self.settings = settings
        self.runtime_config = runtime_config

    async def notify_actionable(
        self,
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
        decision_id: int,
        require_confirmation: bool,
    ) -> None:
        if decision.action == "SKIP":
            return

        runtime = await self.runtime_config.snapshot()
        token = self.settings.admin_telegram_bot_token or runtime.telegram_bot_token
        chat_id = runtime.telegram_chat_id
        if not token or not chat_id:
            return

        is_ru = runtime.user_language.startswith("ru")
        text = self._build_message_ru(analysis, decision, execution, require_confirmation)
        if not is_ru:
            text = self._build_message_en(analysis, decision, execution, require_confirmation)

        payload: dict = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if require_confirmation:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": "Отклонить", "callback_data": f"decision:reject:{decision_id}"},
                        {"text": "Поставить", "callback_data": f"decision:approve:{decision_id}"},
                    ]
                ]
            }

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as exc:
            logger.exception("Telegram actionable notify failed: %s", exc)

    @staticmethod
    def _build_message_ru(
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
        require_confirmation: bool,
    ) -> str:
        event = analysis.packet.world_event
        market = decision.market.question if decision.market else "N/A"
        side = "ДА" if decision.action == "BET_YES" else "НЕТ"
        mode_text = "Требуется подтверждение" if require_confirmation else "Автоисполнение включено"
        lines = [
            "Сигнал для ставки (Polymarket-style)",
            f"Событие: {event.title}",
            f"Источник: {event.source}",
            f"Ссылка: {event.url}",
            f"Приоритет: {analysis.packet.priority_score:.2f} ({analysis.packet.priority_reason})",
            f"Рынок: {market}",
            f"Сторона: {side}",
            f"Ставка: ${decision.stake_usd:.2f}",
            f"Уверенность: {decision.confidence:.2f}",
            f"Обоснование: {decision.rationale}",
            f"Статус исполнения: {execution.message}",
            f"Режим: {mode_text}",
        ]
        return "\n".join(lines)

    @staticmethod
    def _build_message_en(
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
        require_confirmation: bool,
    ) -> str:
        event = analysis.packet.world_event
        market = decision.market.question if decision.market else "N/A"
        side = "YES" if decision.action == "BET_YES" else "NO"
        mode_text = "Manual confirmation required" if require_confirmation else "Auto-execution enabled"
        lines = [
            "Bet signal (Polymarket-style)",
            f"Event: {event.title}",
            f"Source: {event.source}",
            f"URL: {event.url}",
            f"Priority: {analysis.packet.priority_score:.2f} ({analysis.packet.priority_reason})",
            f"Market: {market}",
            f"Side: {side}",
            f"Stake: ${decision.stake_usd:.2f}",
            f"Confidence: {decision.confidence:.2f}",
            f"Rationale: {decision.rationale}",
            f"Execution: {execution.message}",
            f"Mode: {mode_text}",
        ]
        return "\n".join(lines)
