import logging

import httpx

from app.config import Settings
from app.models import Decision
from app.services.execution import ExecutionService
from app.services.runtime_config import RuntimeConfigService
from app.services.storage import Storage

logger = logging.getLogger(__name__)

ALLOWED_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DRY_RUN",
    "AUTO_EXECUTE",
    "MAX_BET_USD",
    "MIN_CONFIDENCE",
    "MAX_DAILY_LOSS_USD",
    "MIN_MARKET_VOLUME",
    "INITIAL_BANKROLL_USD",
    "USER_LANGUAGE",
    "LLM_1_NAME",
    "LLM_1_BASE_URL",
    "LLM_1_MODEL",
    "LLM_1_API_KEY",
    "LLM_2_NAME",
    "LLM_2_BASE_URL",
    "LLM_2_MODEL",
    "LLM_2_API_KEY",
    "LLM_3_NAME",
    "LLM_3_BASE_URL",
    "LLM_3_MODEL",
    "LLM_3_API_KEY",
}

SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "LLM_1_API_KEY",
    "LLM_2_API_KEY",
    "LLM_3_API_KEY",
}


class TelegramAdminBot:
    def __init__(
        self,
        settings: Settings,
        runtime_config: RuntimeConfigService,
        storage: Storage,
        execution: ExecutionService,
    ):
        self.settings = settings
        self.runtime_config = runtime_config
        self.storage = storage
        self.execution = execution
        self._offset = 0

    def enabled(self) -> bool:
        return bool(self.settings.admin_telegram_bot_token and self.settings.admin_telegram_user_id)

    async def poll_once(self) -> None:
        if not self.enabled():
            return

        updates = await self._get_updates()
        for upd in updates:
            self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
            await self._handle_update(upd)

    async def _handle_update(self, upd: dict) -> None:
        callback = upd.get("callback_query")
        if callback:
            user_id = int((callback.get("from") or {}).get("id", 0))
            chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
            data = str(callback.get("data") or "")
            callback_id = callback.get("id")
            if user_id != self.settings.admin_telegram_user_id:
                if chat_id:
                    await self._send_message(chat_id, "Доступ запрещен.")
                if callback_id:
                    await self._answer_callback(callback_id, "Доступ запрещен")
                return
            if callback_id:
                await self._answer_callback(callback_id, "Принято")
            if chat_id:
                await self._handle_callback(chat_id, data)
            return

        msg = upd.get("message") or {}
        user = msg.get("from") or {}
        user_id = int(user.get("id", 0))
        chat_id = msg.get("chat", {}).get("id")
        text = str(msg.get("text") or "").strip()
        language_code = str(user.get("language_code") or "").strip().lower()
        if not text or not chat_id:
            return

        if user_id != self.settings.admin_telegram_user_id:
            await self._send_message(chat_id, "Доступ запрещен.")
            return

        if language_code:
            await self.runtime_config.set_value("USER_LANGUAGE", language_code)

        reply = await self._handle_command(chat_id, text)
        if reply:
            await self._send_message(chat_id, reply)

    async def _handle_callback(self, chat_id: int | str, data: str) -> None:
        if data.startswith("decision:approve:"):
            decision_id = int(data.rsplit(":", 1)[1])
            row = await self.storage.get_decision(decision_id)
            if not row:
                await self._send_message(chat_id, "Ставка не найдена.")
                return
            if row.get("decision_state") != "pending_approval":
                await self._send_message(chat_id, "Эта ставка уже обработана.")
                return

            decision = Decision(
                action=str(row.get("action", "SKIP")),
                stake_usd=float(row.get("stake_usd") or 0.0),
                confidence=float(row.get("confidence") or 0.0),
                rationale=str(row.get("rationale") or ""),
            )
            execution = await self.execution.execute(decision)
            await self.storage.update_decision_state(
                decision_id=decision_id,
                decision_state="executed",
                execution_success=execution.success,
                execution_message=execution.message,
            )
            await self._send_message(
                chat_id, f"Ставка #{decision_id} подтверждена. {execution.message}"
            )
            return

        if data.startswith("decision:reject:"):
            decision_id = int(data.rsplit(":", 1)[1])
            await self.storage.update_decision_state(
                decision_id=decision_id,
                decision_state="rejected",
                execution_success=False,
                execution_message="Отклонено пользователем в админ-панели.",
            )
            await self._send_message(chat_id, f"Ставка #{decision_id} отклонена.")
            return

        if data == "menu:stats":
            await self._send_message(chat_id, await self._stats_text())
            return
        if data == "menu:open":
            await self._send_message(chat_id, await self._open_positions_text())
            return
        if data == "menu:history":
            await self._send_message(chat_id, await self._history_text())
            return
        if data == "menu:pending":
            await self._send_message(chat_id, await self._pending_text())
            return
        if data == "menu:toggle_mode":
            runtime = await self.runtime_config.snapshot()
            if runtime.auto_execute:
                await self.runtime_config.set_value("AUTO_EXECUTE", "false")
                await self.runtime_config.set_value("DRY_RUN", "true")
                await self._send_message(chat_id, "Режим переключен: ручное подтверждение.")
            else:
                await self.runtime_config.set_value("AUTO_EXECUTE", "true")
                await self.runtime_config.set_value("DRY_RUN", "false")
                await self._send_message(chat_id, "Режим переключен: автоисполнение.")
            return

    async def _handle_command(self, chat_id: int | str, text: str) -> str:
        if text in {"/start", "/help"}:
            await self._send_message(chat_id, "Панель управления:", reply_markup=self._panel_keyboard())
            return (
                "Команды:\n"
                "/status - текущие настройки\n"
                "/panel - кнопки управления\n"
                "/keys - список изменяемых ключей\n"
                "/set KEY VALUE - изменить настройку\n"
                "/show KEY - показать значение ключа\n"
                "/mode auto - автоисполнение\n"
                "/mode manual - ручное подтверждение\n"
                "/settle ID win|loss [pnl_usd] - закрыть ставку вручную"
            )

        if text == "/panel":
            await self._send_message(chat_id, "Открываю панель:", reply_markup=self._panel_keyboard())
            return ""

        if text == "/keys":
            return "Изменяемые ключи:\n" + "\n".join(sorted(ALLOWED_KEYS))

        if text == "/status":
            return await self._status_text()

        if text == "/mode manual":
            await self.runtime_config.set_value("AUTO_EXECUTE", "false")
            await self.runtime_config.set_value("DRY_RUN", "true")
            return "Режим: ручное подтверждение."

        if text == "/mode auto":
            await self.runtime_config.set_value("AUTO_EXECUTE", "true")
            await self.runtime_config.set_value("DRY_RUN", "false")
            return "Режим: автоисполнение."

        if text.startswith("/show "):
            parts = text.split(maxsplit=1)
            key = parts[1].strip().upper()
            if key not in ALLOWED_KEYS:
                return "Неизвестный ключ. Используй /keys."
            overrides = await self.runtime_config.overrides()
            val = overrides.get(key, "<значение по умолчанию>")
            if key in SECRET_KEYS and val != "<значение по умолчанию>":
                val = self._mask(val)
            return f"{key}={val}"

        if text.startswith("/set "):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                return "Использование: /set KEY VALUE"
            key = parts[1].strip().upper()
            value = parts[2].strip()
            if key not in ALLOWED_KEYS:
                return "Ключ не разрешен. Используй /keys."
            await self.runtime_config.set_value(key, value, is_secret=key in SECRET_KEYS)
            shown = self._mask(value) if key in SECRET_KEYS else value
            return f"Сохранено: {key}={shown}"

        if text.startswith("/settle "):
            parts = text.split()
            if len(parts) < 3:
                return "Использование: /settle ID win|loss [pnl_usd]"
            decision_id = int(parts[1])
            result = parts[2].lower()
            pnl = float(parts[3]) if len(parts) > 3 else 0.0
            if result == "win":
                state = "settled_win"
                if pnl <= 0:
                    row = await self.storage.get_decision(decision_id)
                    pnl = float(row["stake_usd"]) if row else 0.0
            elif result == "loss":
                state = "settled_loss"
                if pnl >= 0:
                    row = await self.storage.get_decision(decision_id)
                    pnl = -float(row["stake_usd"]) if row else 0.0
            else:
                return "Результат должен быть win или loss."
            await self.storage.update_decision_state(
                decision_id=decision_id,
                decision_state=state,  # type: ignore[arg-type]
                execution_message=f"Сделка вручную закрыта: {result}",
                pnl_delta=pnl,
            )
            return f"Ставка #{decision_id} закрыта как {result}, PnL={pnl:.2f} USD."

        return "Неизвестная команда. Используй /help."

    async def _status_text(self) -> str:
        snapshot = await self.runtime_config.snapshot()
        return (
            "Текущий статус:\n"
            f"DRY_RUN={snapshot.dry_run}\n"
            f"AUTO_EXECUTE={snapshot.auto_execute}\n"
            f"MAX_BET_USD={snapshot.max_bet_usd}\n"
            f"MIN_CONFIDENCE={snapshot.min_confidence}\n"
            f"MAX_DAILY_LOSS_USD={snapshot.max_daily_loss_usd}\n"
            f"MIN_MARKET_VOLUME={snapshot.min_market_volume}\n"
            f"INITIAL_BANKROLL_USD={snapshot.initial_bankroll_usd}\n"
            f"USER_LANGUAGE={snapshot.user_language}\n"
            f"LLM_ENABLED={len(snapshot.llms)}"
        )

    async def _stats_text(self) -> str:
        snapshot = await self.runtime_config.snapshot()
        stats = await self.storage.stats(snapshot.initial_bankroll_usd)
        return (
            "Статистика портфеля:\n"
            f"Стартовый портфель: ${stats.initial_bankroll_usd:.2f}\n"
            f"Текущий портфель: ${stats.current_bankroll_usd:.2f}\n"
            f"PnL: ${stats.pnl_usd:.2f}\n"
            f"Всего ставок: {stats.total_bets}\n"
            f"Побед: {stats.wins}\n"
            f"Поражений: {stats.losses}\n"
            f"Win rate: {stats.win_rate * 100:.2f}%\n"
            f"Ожидают подтверждения: {stats.pending_approval}\n"
            f"Открытых позиций: {stats.open_positions}"
        )

    async def _open_positions_text(self) -> str:
        rows = await self.storage.list_open_positions(limit=15)
        if not rows:
            return "Открытых ставок нет."
        lines = ["Текущие ставки:"]
        for r in rows:
            lines.append(
                f"#{r['id']} | {r['action']} | ${float(r['stake_usd']):.2f} | "
                f"{r.get('market_question') or '-'}"
            )
        return "\n".join(lines)[:3900]

    async def _history_text(self) -> str:
        rows = await self.storage.list_recent_history(limit=15)
        if not rows:
            return "История ставок пока пуста."
        lines = ["История ставок:"]
        for r in rows:
            lines.append(
                f"#{r['id']} | {r['decision_state']} | ${float(r['stake_usd']):.2f} | "
                f"PnL ${float(r.get('pnl_usd') or 0):.2f}"
            )
        return "\n".join(lines)[:3900]

    async def _pending_text(self) -> str:
        rows = await self.storage.list_pending_approvals(limit=15)
        if not rows:
            return "Нет ставок, ожидающих подтверждения."
        lines = ["Ожидают подтверждения:"]
        for r in rows:
            lines.append(
                f"#{r['id']} | {r['action']} | ${float(r['stake_usd']):.2f} | "
                f"{r.get('market_question') or '-'}"
            )
        return "\n".join(lines)[:3900]

    async def _get_updates(self) -> list[dict]:
        url = f"https://api.telegram.org/bot{self.settings.admin_telegram_bot_token}/getUpdates"
        params = {"offset": self._offset, "timeout": 0, "limit": 30}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if data.get("ok"):
                    return list(data.get("result") or [])
        except Exception as exc:
            logger.exception("Admin bot getUpdates failed: %s", exc)
        return []

    async def _send_message(
        self, chat_id: int | str, text: str, reply_markup: dict | None = None
    ) -> None:
        url = f"https://api.telegram.org/bot{self.settings.admin_telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as exc:
            logger.exception("Admin bot sendMessage failed: %s", exc)

    async def _answer_callback(self, callback_id: str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.settings.admin_telegram_bot_token}/answerCallbackQuery"
        payload = {"callback_query_id": callback_id, "text": text[:180], "show_alert": False}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as exc:
            logger.exception("Answer callback failed: %s", exc)

    @staticmethod
    def _panel_keyboard() -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "Статистика", "callback_data": "menu:stats"},
                    {"text": "Текущие ставки", "callback_data": "menu:open"},
                ],
                [
                    {"text": "История ставок", "callback_data": "menu:history"},
                    {"text": "Ожидают решения", "callback_data": "menu:pending"},
                ],
                [
                    {
                        "text": "Переключить авто/ручной режим",
                        "callback_data": "menu:toggle_mode",
                    }
                ],
            ]
        }

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        return value[:3] + "*" * (len(value) - 6) + value[-3:]
