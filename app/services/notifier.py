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
        runtime = await self.runtime_config.snapshot()
        is_ru = runtime.user_language.startswith("ru")

        info_token = runtime.telegram_bot_token or self.settings.telegram_bot_token
        info_chat_id = runtime.telegram_chat_id
        if info_token and info_chat_id:
            info_text = self._build_info_signal_message_ru(analysis, decision) if is_ru else self._build_info_signal_message_en(analysis, decision)
            await self._send_message(info_token, info_chat_id, info_text)

        admin_token = self.settings.admin_telegram_bot_token
        admin_chat_id = self.settings.admin_telegram_user_id
        if not admin_token or not admin_chat_id:
            return

        admin_text = self._build_message_ru(analysis, decision, execution, require_confirmation)
        if not is_ru:
            admin_text = self._build_message_en(analysis, decision, execution, require_confirmation)
        reply_markup = None
        if require_confirmation and decision.action != "NO_BET":
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "Отклонить", "callback_data": f"decision:reject:{decision_id}"},
                        {"text": "Принять ставку", "callback_data": f"decision:approve:{decision_id}"},
                    ]
                ]
            }

        await self._send_message(admin_token, admin_chat_id, admin_text, reply_markup=reply_markup)

    async def _send_message(
        self,
        token: str,
        chat_id: int | str,
        text: str,
        reply_markup: dict | None = None,
    ) -> None:
        payload: dict = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as exc:
            logger.exception("Telegram notify failed: %s", exc)

    @staticmethod
    def _build_info_signal_message_ru(analysis: AggregatedAnalysis, decision: Decision) -> str:
        market_title = decision.market.question if decision.market else "N/A"
        market_url = decision.market.url if decision.market and decision.market.url else "N/A"
        if decision.action == "BET_YES":
            ai_decision = "СТАВИТЬ (YES)"
        elif decision.action == "BET_NO":
            ai_decision = "СТАВИТЬ (NO)"
        else:
            ai_decision = "НЕ СТАВИТЬ"

        risks = []
        for output in analysis.model_outputs:
            for risk in output.risks:
                if risk not in risks:
                    risks.append(risk)
        risk_text = ", ".join(TelegramNotifier._translate_risk(r) for r in risks[:4]) if risks else "умеренный"

        return "\n".join(
            [
                "🚨 НОВЫЙ СИГНАЛ",
                f"📌 Название: {market_title}",
                f"🔗 Ссылка на Polymarket: {market_url}",
                f"🧠 Решение ИИ: {ai_decision}",
                f"⚠️ Риск: {risk_text}",
            ]
        )

    @staticmethod
    def _build_info_signal_message_en(analysis: AggregatedAnalysis, decision: Decision) -> str:
        market_title = decision.market.question if decision.market else "N/A"
        market_url = decision.market.url if decision.market and decision.market.url else "N/A"
        if decision.action == "BET_YES":
            ai_decision = "BET (YES)"
        elif decision.action == "BET_NO":
            ai_decision = "BET (NO)"
        else:
            ai_decision = "NO BET"

        risks = []
        for output in analysis.model_outputs:
            for risk in output.risks:
                if risk not in risks:
                    risks.append(risk)
        risk_text = ", ".join(risks[:4]) if risks else "moderate"

        return "\n".join(
            [
                "🚨 NEW SIGNAL",
                f"📌 Signal: {market_title}",
                f"🔗 Polymarket link: {market_url}",
                f"🧠 AI decision: {ai_decision}",
                f"⚠️ Risk: {risk_text}",
            ]
        )

    @staticmethod
    def _build_message_ru(
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
        require_confirmation: bool,
    ) -> str:
        event = analysis.packet.world_event
        market = decision.market.question if decision.market else "N/A"
        if decision.action == "BET_YES":
            side = "СТАВИТЬ (YES)"
        elif decision.action == "BET_NO":
            side = "СТАВИТЬ (NO)"
        else:
            side = "НЕ СТАВИТЬ"
        mode_text = "Требуется подтверждение" if require_confirmation else "Автоисполнение включено"
        lines = [
            "Сигнал для ставки (Polymarket-style)",
            f"Событие: {event.title}",
            f"Источник: {event.source}",
            f"Ссылка: {event.url}",
            f"Приоритет: {analysis.packet.priority_score:.2f} ({analysis.packet.priority_reason})",
            f"Почему предложено: {analysis.packet.priority_reason}. "
            f"Сигнал сформирован по совпадению новости с рынком и риск-фильтрам.",
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
        if decision.action == "BET_YES":
            side = "BET (YES)"
        elif decision.action == "BET_NO":
            side = "BET (NO)"
        else:
            side = "NO BET"
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

    # ------------------------------------------------------------------
    # Уведомление о продаже позиции
    # ------------------------------------------------------------------
    async def notify_position_action(
        self,
        position: dict,
        action: str,
        current_price: float,
        pnl_pct: float,
        simulated: bool,
    ) -> None:
        runtime = await self.runtime_config.snapshot()
        info_token = runtime.telegram_bot_token or self.settings.telegram_bot_token
        info_chat_id = runtime.telegram_chat_id
        admin_token = self.settings.admin_telegram_bot_token
        admin_chat_id = self.settings.admin_telegram_user_id

        verb = "Симуляция продажи" if simulated else "ПРОДАЖА ПОЗИЦИИ"
        sign = "+" if pnl_pct >= 0 else ""
        text = "\n".join([
            f"💰 {verb}",
            f"📌 Рынок: {position.get('market_question', 'N/A')}",
            f"🔗 {position.get('market_url', '')}",
            f"📊 Сторона: {position.get('action', 'N/A')}",
            f"💵 Текущая цена: {current_price:.3f}",
            f"{'📈' if pnl_pct >= 0 else '📉'} P&L: {sign}{pnl_pct:.1f}%",
            f"🤖 Причина: {action}",
        ])

        if info_token and info_chat_id:
            await self._send_message(info_token, info_chat_id, text)
        if admin_token and admin_chat_id:
            await self._send_message(admin_token, admin_chat_id, text)

    # ------------------------------------------------------------------
    # BTC-сигнал
    # ------------------------------------------------------------------
    async def notify_btc_signal(
        self,
        direction: str,
        confidence: float,
        stake_usd: float,
        rationale: str,
        market_url: str,
        price_source: str,
        price_source_url: str,
        require_confirmation: bool,
        decision_id: int,
    ) -> None:
        runtime = await self.runtime_config.snapshot()
        info_token = runtime.telegram_bot_token or self.settings.telegram_bot_token
        info_chat_id = runtime.telegram_chat_id
        admin_token = self.settings.admin_telegram_bot_token
        admin_chat_id = self.settings.admin_telegram_user_id

        arrow = "⬆️ РОСТ" if direction == "YES" else "⬇️ ПАДЕНИЕ"
        mode_text = "Требуется подтверждение" if require_confirmation else "Автоисполнение"
        text = "\n".join([
            "⚡ BTC СИГНАЛ (5 мин)",
            f"🎯 Направление: {arrow}",
            f"🔗 Рынок: {market_url}",
            f"🧭 Источник цены: {price_source}",
            f"📎 Источник: {price_source_url}",
            f"📊 Уверенность: {confidence:.0%}",
            f"💵 Ставка: ${stake_usd:.2f}",
            f"🧠 Обоснование: {rationale[:300]}",
            f"⚙️ Режим: {mode_text}",
        ])

        if info_token and info_chat_id:
            await self._send_message(info_token, info_chat_id, text)

        if admin_token and admin_chat_id:
            reply_markup = None
            if require_confirmation:
                reply_markup = {
                    "inline_keyboard": [[
                        {"text": "Отклонить", "callback_data": f"decision:reject:{decision_id}"},
                        {"text": "Принять ставку", "callback_data": f"decision:approve:{decision_id}"},
                    ]]
                }
            await self._send_message(admin_token, admin_chat_id, text, reply_markup=reply_markup)

    @staticmethod
    def _translate_risk(risk: str) -> str:
        """Переводит английские коды ошибок/рисков в читаемый русский текст."""
        _MAP = {
            "model_error": "ошибка модели",
            "ошибка_модели": "ошибка модели",
            "no_models_configured": "модели не настроены",
            "модели_не_настроены": "модели не настроены",
            "timeout_error": "таймаут запроса",
            "таймаут_модели": "таймаут запроса к модели",
            "http_error_401": "ошибка авторизации модели (401)",
            "http_error_429": "превышен лимит запросов к модели (429)",
            "http_error_500": "ошибка сервера модели (500)",
            "ошибка_формата_ответа": "модель вернула некорректный формат",
            "low_volume": "низкий объём рынка",
            "high_uncertainty": "высокая неопределённость",
            "conflicting_signals": "противоречивые сигналы",
            "news_gap": "недостаточно новостей",
            "moderate": "умеренный",
            "no_market": "рынок не найден",
            "market_closed": "рынок закрыт",
            "price_stale": "устаревшие котировки",
            "binance_error": "ошибка получения цены BTC",
            "price_source_error": "ошибка источника цены BTC",
        }
        return _MAP.get(risk, risk)
