import logging
from typing import Optional

import httpx

from app.config import Settings
from app.models import Decision, PolymarketMarket
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
    "POLYMARKET_CLOB_HOST",
    "POLYMARKET_CHAIN_ID",
    "POLYMARKET_SIGNATURE_TYPE",
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_FUNDER_ADDRESS",
}

SECRET_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "LLM_1_API_KEY",
    "POLYMARKET_PRIVATE_KEY",
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
        self._chat_state: dict[int, str] = {}

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

        state_reply = await self._consume_state(int(chat_id), text)
        if state_reply is not None:
            await self._send_message(chat_id, state_reply)
            return

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
                market=PolymarketMarket(
                    market_id=str(row.get("market_id") or ""),
                    question=str(row.get("market_question") or ""),
                    url=str(row.get("market_url") or "") or None,
                ),
                action=str(row.get("action", "SKIP")),
                stake_usd=float(row.get("stake_usd") or 0.0),
                confidence=float(row.get("confidence") or 0.0),
                rationale=str(row.get("rationale") or ""),
            )
            execution = await self.execution.execute(decision, market_id=str(row.get("market_id") or ""))
            new_state = "executed" if execution.success else "rejected"
            await self.storage.update_decision_state(
                decision_id=decision_id,
                decision_state=new_state,  # type: ignore[arg-type]
                execution_success=execution.success,
                execution_message=execution.message,
            )
            if execution.success:
                await self._send_message(chat_id, f"Ставка #{decision_id} подтверждена. {execution.message}")
            else:
                await self._send_message(chat_id, f"Ставка #{decision_id} отклонена. {execution.message}")
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

        if data.startswith("pending:view:"):
            decision_id = int(data.rsplit(":", 1)[1])
            await self._send_pending_decision_card(chat_id, decision_id)
            return

        if data.startswith("open:view:"):
            decision_id = int(data.rsplit(":", 1)[1])
            await self._send_open_position_card(chat_id, decision_id)
            return

        if data.startswith("open:settle:"):
            parts = data.split(":")
            if len(parts) != 4:
                return
            outcome = parts[2]
            decision_id = int(parts[3])
            row = await self.storage.get_decision(decision_id)
            if not row:
                await self._send_message(chat_id, "Ставка не найдена.")
                return
            stake = float(row.get("stake_usd") or 0.0)
            if outcome == "win":
                state = "settled_win"
                pnl = abs(stake)
            else:
                state = "settled_loss"
                pnl = -abs(stake)
            await self.storage.update_decision_state(
                decision_id=decision_id,
                decision_state=state,  # type: ignore[arg-type]
                execution_success=True,
                execution_message=f"Позиция закрыта вручную: {outcome}.",
                pnl_delta=pnl,
            )
            await self._send_message(chat_id, f"Позиция #{decision_id} закрыта как {outcome}, PnL={pnl:.2f} USD.")
            return

        if data == "menu:stats":
            await self._send_message(chat_id, await self._stats_text())
            return
        if data == "menu:open":
            await self._send_open_positions_cards(chat_id)
            return
        if data == "menu:history":
            await self._send_message(chat_id, await self._history_text())
            return
        if data == "menu:pending":
            await self._send_pending_cards(chat_id)
            return
        if data == "menu:settings":
            await self._send_message(chat_id, "Настройки бота:", reply_markup=self._settings_keyboard())
            return
        if data == "menu:llm_setup":
            self._chat_state[int(chat_id)] = "awaiting_llm_1"
            await self._send_message(
                chat_id,
                "Отправь настройки для единственной LLM (ChatGPT 5.3) в формате:\n"
                "name=ChatGPT 5.3\n"
                "base_url=https://api.openai.com/v1\n"
                "model=gpt-5.3\n"
                "api_key=sk-...\n\n"
                "Можно также одной строкой через | :\n"
                "ChatGPT 5.3|https://api.openai.com/v1|gpt-5.3|sk-...",
            )
            return
        if data == "menu:llm_show":
            await self._send_message(chat_id, await self._llm_status_text())
            return
        if data == "menu:pm_setup":
            self._chat_state[int(chat_id)] = "awaiting_polymarket"
            await self._send_message(
                chat_id,
                "Отправь настройки Polymarket в формате:\n"
                "clob_host=https://clob.polymarket.com\n"
                "chain_id=137\n"
                "signature_type=1\n"
                "private_key=0x...\n"
                "funder_address=0x...",
            )
            return
        if data == "menu:pm_show":
            await self._send_message(chat_id, await self._polymarket_status_text())
            return
        if data == "menu:toggle_dry_run":
            runtime = await self.runtime_config.snapshot()
            new_val = "false" if runtime.dry_run else "true"
            await self.runtime_config.set_value("DRY_RUN", new_val)
            await self._send_message(chat_id, f"DRY_RUN переключен: {new_val}")
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
                "/llm - мастер настройки единственной LLM\n"
                "/pm - мастер привязки Polymarket\n"
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

        if text == "/llm":
            self._chat_state[int(chat_id)] = "awaiting_llm_1"
            return (
                "Отправь настройки LLM в формате:\n"
                "name=ChatGPT 5.3\n"
                "base_url=https://api.openai.com/v1\n"
                "model=gpt-5.3\n"
                "api_key=sk-..."
            )

        if text == "/pm":
            self._chat_state[int(chat_id)] = "awaiting_polymarket"
            return (
                "Отправь настройки Polymarket в формате:\n"
                "clob_host=https://clob.polymarket.com\n"
                "chain_id=137\n"
                "signature_type=1\n"
                "private_key=0x...\n"
                "funder_address=0x..."
            )

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
            f"LLM_ENABLED={len(snapshot.llms)} (используется только 1 LLM)\n"
            f"POLYMARKET_BOUND={bool(snapshot.polymarket_private_key and snapshot.polymarket_funder_address)}"
        )

    async def _llm_status_text(self) -> str:
        snapshot = await self.runtime_config.snapshot()
        if not snapshot.llms:
            return "LLM не настроена. Нажми кнопку 'Добавить 1 LLM'."
        llm = snapshot.llms[0]
        return (
            "Текущая LLM:\n"
            f"name={llm.name}\n"
            f"base_url={llm.base_url}\n"
            f"model={llm.model}\n"
            f"api_key={self._mask(llm.api_key)}"
        )

    async def _polymarket_status_text(self) -> str:
        snapshot = await self.runtime_config.snapshot()
        return (
            "Polymarket:\n"
            f"clob_host={snapshot.polymarket_clob_host}\n"
            f"chain_id={snapshot.polymarket_chain_id}\n"
            f"signature_type={snapshot.polymarket_signature_type}\n"
            f"private_key={self._mask(snapshot.polymarket_private_key) if snapshot.polymarket_private_key else '<empty>'}\n"
            f"funder_address={snapshot.polymarket_funder_address or '<empty>'}"
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

    async def _send_open_positions_cards(self, chat_id: int | str) -> None:
        rows = await self.storage.list_open_positions(limit=10)
        if not rows:
            await self._send_message(chat_id, "Открытых ставок нет.")
            return
        await self._send_message(chat_id, "Открытые позиции: выбери карточку")
        for row in rows:
            await self._send_message(
                chat_id,
                self._format_row_card(row),
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "Открыть карточку", "callback_data": f"open:view:{row['id']}"}],
                    ]
                },
            )

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

    async def _send_pending_cards(self, chat_id: int | str) -> None:
        rows = await self.storage.list_pending_approvals(limit=10)
        if not rows:
            await self._send_message(chat_id, "Нет ставок, ожидающих подтверждения.")
            return
        await self._send_message(chat_id, "Ожидают решения: выбери карточку")
        for row in rows:
            await self._send_message(
                chat_id,
                self._format_row_card(row),
                reply_markup={
                    "inline_keyboard": [
                        [{"text": "Открыть карточку", "callback_data": f"pending:view:{row['id']}"}],
                    ]
                },
            )

    async def _send_pending_decision_card(self, chat_id: int | str, decision_id: int) -> None:
        row = await self.storage.get_decision(decision_id)
        if not row:
            await self._send_message(chat_id, "Ставка не найдена.")
            return
        if row.get("decision_state") != "pending_approval":
            await self._send_message(chat_id, "Эта ставка уже обработана.")
            return
        await self._send_message(
            chat_id,
            self._format_row_card(row),
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Отклонить", "callback_data": f"decision:reject:{decision_id}"},
                        {"text": "Принять ставку", "callback_data": f"decision:approve:{decision_id}"},
                    ]
                ]
            },
        )

    async def _send_open_position_card(self, chat_id: int | str, decision_id: int) -> None:
        row = await self.storage.get_decision(decision_id)
        if not row:
            await self._send_message(chat_id, "Позиция не найдена.")
            return
        await self._send_message(
            chat_id,
            self._format_row_card(row),
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Закрыть WIN", "callback_data": f"open:settle:win:{decision_id}"},
                        {"text": "Закрыть LOSS", "callback_data": f"open:settle:loss:{decision_id}"},
                    ]
                ]
            },
        )

    @staticmethod
    def _format_row_card(row: dict) -> str:
        event_title = str(row.get("event_title") or "-")
        event_url = str(row.get("event_url") or "-")
        market_question = str(row.get("market_question") or "-")
        market_url = str(row.get("market_url") or "-")
        action = str(row.get("action") or "-")
        state = str(row.get("decision_state") or "-")
        msg = str(row.get("execution_message") or "")
        stake = float(row.get("stake_usd") or 0.0)
        confidence = float(row.get("confidence") or 0.0)
        return (
            f"ID: #{row.get('id')}\n"
            f"Статус: {state}\n"
            f"Сигнал: {action} | ${stake:.2f} | conf={confidence:.2f}\n"
            f"Событие: {event_title}\n"
            f"Новости: {event_url}\n"
            f"Рынок: {market_question}\n"
            f"Рынок URL: {market_url}\n"
            f"Сообщение: {msg}"
        )[:3900]

    async def _consume_state(self, chat_id: int, text: str) -> Optional[str]:
        state = self._chat_state.get(chat_id)
        if state == "awaiting_polymarket":
            parsed_pm = self._parse_polymarket_payload(text)
            if not parsed_pm:
                return (
                    "Не удалось распознать формат Polymarket. Отправь:\n"
                    "clob_host=https://clob.polymarket.com\n"
                    "chain_id=137\n"
                    "signature_type=1\n"
                    "private_key=0x...\n"
                    "funder_address=0x..."
                )

            await self.runtime_config.set_value("POLYMARKET_CLOB_HOST", parsed_pm["clob_host"])
            await self.runtime_config.set_value("POLYMARKET_CHAIN_ID", parsed_pm["chain_id"])
            await self.runtime_config.set_value("POLYMARKET_SIGNATURE_TYPE", parsed_pm["signature_type"])
            await self.runtime_config.set_value(
                "POLYMARKET_PRIVATE_KEY", parsed_pm["private_key"], is_secret=True
            )
            await self.runtime_config.set_value("POLYMARKET_FUNDER_ADDRESS", parsed_pm["funder_address"])
            self._chat_state.pop(chat_id, None)
            return (
                "Polymarket привязан.\n"
                f"clob_host={parsed_pm['clob_host']}\n"
                f"chain_id={parsed_pm['chain_id']}\n"
                f"signature_type={parsed_pm['signature_type']}\n"
                f"private_key={self._mask(parsed_pm['private_key'])}\n"
                f"funder_address={parsed_pm['funder_address']}"
            )

        if state != "awaiting_llm_1":
            return None

        parsed = self._parse_llm_payload(text)
        if not parsed:
            return (
                "Не удалось распознать формат. Отправь снова:\n"
                "name=ChatGPT 5.3\n"
                "base_url=https://api.openai.com/v1\n"
                "model=gpt-5.3\n"
                "api_key=sk-..."
            )

        await self.runtime_config.set_value("LLM_1_NAME", parsed["name"])
        await self.runtime_config.set_value("LLM_1_BASE_URL", parsed["base_url"])
        await self.runtime_config.set_value("LLM_1_MODEL", parsed["model"])
        await self.runtime_config.set_value("LLM_1_API_KEY", parsed["api_key"], is_secret=True)
        self._chat_state.pop(chat_id, None)
        return (
            "LLM сохранена.\n"
            f"name={parsed['name']}\n"
            f"base_url={parsed['base_url']}\n"
            f"model={parsed['model']}\n"
            f"api_key={self._mask(parsed['api_key'])}"
        )

    @staticmethod
    def _parse_llm_payload(text: str) -> Optional[dict[str, str]]:
        if "|" in text:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 4:
                return {
                    "name": parts[0] or "ChatGPT 5.3",
                    "base_url": parts[1],
                    "model": parts[2] or "gpt-5.3",
                    "api_key": parts[3],
                }

        kv: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            kv[k.strip().lower()] = v.strip()

        base_url = kv.get("base_url", "")
        model = kv.get("model", "")
        api_key = kv.get("api_key", "")
        if not base_url or not model or not api_key:
            return None
        return {
            "name": kv.get("name", "ChatGPT 5.3"),
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
        }

    @staticmethod
    def _parse_polymarket_payload(text: str) -> Optional[dict[str, str]]:
        kv: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            kv[k.strip().lower()] = v.strip()

        required = ["clob_host", "chain_id", "signature_type", "private_key", "funder_address"]
        if any(not kv.get(k) for k in required):
            return None
        return {
            "clob_host": kv["clob_host"],
            "chain_id": kv["chain_id"],
            "signature_type": kv["signature_type"],
            "private_key": kv["private_key"],
            "funder_address": kv["funder_address"],
        }

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
                [
                    {
                        "text": "Настройки и LLM",
                        "callback_data": "menu:settings",
                    }
                ],
            ]
        }

    @staticmethod
    def _settings_keyboard() -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "Добавить 1 LLM", "callback_data": "menu:llm_setup"},
                    {"text": "Показать LLM", "callback_data": "menu:llm_show"},
                ],
                [
                    {"text": "Привязать Polymarket", "callback_data": "menu:pm_setup"},
                    {"text": "Статус Polymarket", "callback_data": "menu:pm_show"},
                ],
                [
                    {"text": "Переключить DRY_RUN", "callback_data": "menu:toggle_dry_run"},
                    {"text": "Переключить AUTO_EXECUTE", "callback_data": "menu:toggle_mode"},
                ],
                [{"text": "Ожидают решения", "callback_data": "menu:pending"}],
            ]
        }

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        return value[:3] + "*" * (len(value) - 6) + value[-3:]
