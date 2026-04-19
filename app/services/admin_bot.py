import logging
from typing import Optional

import httpx

from app.config import Settings
from app.services.runtime_config import RuntimeConfigService

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
    def __init__(self, settings: Settings, runtime_config: RuntimeConfigService):
        self.settings = settings
        self.runtime_config = runtime_config
        self._offset = 0

    def enabled(self) -> bool:
        return bool(self.settings.admin_telegram_bot_token and self.settings.admin_telegram_user_id)

    async def poll_once(self) -> None:
        if not self.enabled():
            return

        updates = await self._get_updates()
        for upd in updates:
            self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or {}
            user = msg.get("from") or {}
            user_id = int(user.get("id", 0))
            chat_id = msg.get("chat", {}).get("id")
            text = str(msg.get("text") or "").strip()
            if not text or not chat_id:
                continue

            if user_id != self.settings.admin_telegram_user_id:
                await self._send_message(chat_id, "Access denied.")
                continue

            reply = await self._handle_command(text)
            if reply:
                await self._send_message(chat_id, reply)

    async def _handle_command(self, text: str) -> str:
        if text in {"/start", "/help"}:
            return (
                "Admin commands:\n"
                "/status - show key runtime settings\n"
                "/keys - allowed keys list\n"
                "/set KEY VALUE - set runtime config\n"
                "/mode safe - DRY_RUN=true AUTO_EXECUTE=false\n"
                "/mode live - DRY_RUN=false AUTO_EXECUTE=true\n"
                "/show KEY - show current value (masked for secrets)"
            )

        if text == "/keys":
            return "Allowed keys:\n" + "\n".join(sorted(ALLOWED_KEYS))

        if text == "/status":
            snapshot = await self.runtime_config.snapshot()
            return (
                "Runtime status:\n"
                f"DRY_RUN={snapshot.dry_run}\n"
                f"AUTO_EXECUTE={snapshot.auto_execute}\n"
                f"MAX_BET_USD={snapshot.max_bet_usd}\n"
                f"MIN_CONFIDENCE={snapshot.min_confidence}\n"
                f"MAX_DAILY_LOSS_USD={snapshot.max_daily_loss_usd}\n"
                f"MIN_MARKET_VOLUME={snapshot.min_market_volume}\n"
                f"LLM_ENABLED={len(snapshot.llms)}"
            )

        if text == "/mode safe":
            await self.runtime_config.set_value("DRY_RUN", "true")
            await self.runtime_config.set_value("AUTO_EXECUTE", "false")
            return "Mode switched to SAFE."

        if text == "/mode live":
            await self.runtime_config.set_value("DRY_RUN", "false")
            await self.runtime_config.set_value("AUTO_EXECUTE", "true")
            return "Mode switched to LIVE."

        if text.startswith("/show "):
            parts = text.split(maxsplit=1)
            key = parts[1].strip().upper()
            if key not in ALLOWED_KEYS:
                return "Unknown key. Use /keys."
            overrides = await self.runtime_config.overrides()
            val = overrides.get(key, "<not-overridden>")
            if key in SECRET_KEYS and val != "<not-overridden>":
                val = self._mask(val)
            return f"{key}={val}"

        if text.startswith("/set "):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                return "Usage: /set KEY VALUE"
            key = parts[1].strip().upper()
            value = parts[2].strip()
            if key not in ALLOWED_KEYS:
                return "Key not allowed. Use /keys."
            await self.runtime_config.set_value(key, value, is_secret=key in SECRET_KEYS)
            shown = self._mask(value) if key in SECRET_KEYS else value
            return f"Saved: {key}={shown}"

        return "Unknown command. Use /help."

    async def _get_updates(self) -> list[dict]:
        url = f"https://api.telegram.org/bot{self.settings.admin_telegram_bot_token}/getUpdates"
        params = {"offset": self._offset, "timeout": 0, "limit": 20}
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

    async def _send_message(self, chat_id: int | str, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.settings.admin_telegram_bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
        except Exception as exc:
            logger.exception("Admin bot sendMessage failed: %s", exc)

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        return value[:3] + "*" * (len(value) - 6) + value[-3:]
