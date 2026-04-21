import logging
from datetime import datetime, timezone
from time import monotonic

import httpx

from app.services.polymarket import PolymarketClient
from app.services.runtime_config import RuntimeConfigService

logger = logging.getLogger(__name__)
_ALERT_INTERVAL_SECONDS = 30 * 60


class DependencyHealthService:
    def __init__(
        self,
        runtime_config: RuntimeConfigService,
        polymarket_client: PolymarketClient,
    ):
        self.runtime_config = runtime_config
        self.polymarket_client = polymarket_client
        self._last_alert_ts: dict[str, float] = {}

    async def check_llm(self) -> dict:
        runtime = await self.runtime_config.snapshot()
        llms = runtime.llms
        if not llms:
            msg = "LLM не настроены (пустой runtime.llms)."
            logger.warning("[HEALTH][LLM] %s", msg)
            return {
                "ok": False,
                "message": msg,
                "models": [],
            }

        results = []
        ok_count = 0
        for llm in llms:
            url = llm.base_url.rstrip("/") + "/chat/completions"
            payload = {
                "model": llm.model,
                "messages": [
                    {"role": "system", "content": "You are a health-check probe."},
                    {"role": "user", "content": "Reply with exactly: OK"},
                ],
                "temperature": 0,
                "max_tokens": 8,
            }
            headers = {"Authorization": f"Bearer {llm.api_key}"}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    content = str(data["choices"][0]["message"]["content"]).strip()[:120]
                ok_count += 1
                logger.info("[HEALTH][LLM] OK model=%s response='%s'", llm.name, content)
                results.append(
                    {
                        "name": llm.name,
                        "ok": True,
                        "http_status": 200,
                        "response_preview": content,
                    }
                )
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:300] if exc.response is not None else ""
                logger.error(
                    "[HEALTH][LLM] HTTP error model=%s status=%s body=%s",
                    llm.name,
                    exc.response.status_code if exc.response is not None else "unknown",
                    body,
                )
                results.append(
                    {
                        "name": llm.name,
                        "ok": False,
                        "http_status": exc.response.status_code if exc.response is not None else None,
                        "error": body or str(exc),
                    }
                )
            except Exception as exc:
                logger.exception("[HEALTH][LLM] Unknown error model=%s: %s", llm.name, exc)
                results.append(
                    {
                        "name": llm.name,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        return {
            "ok": ok_count == len(llms),
            "message": f"LLM ok={ok_count}/{len(llms)}",
            "models": results,
        }

    async def check_polymarket(self) -> dict:
        try:
            markets = await self.polymarket_client.fetch_open_markets(limit=10)
            sample = markets[0].question[:120] if markets else ""
            logger.info("[HEALTH][POLYMARKET] OK open_markets=%d sample='%s'", len(markets), sample)
            return {
                "ok": len(markets) > 0,
                "open_markets": len(markets),
                "sample_question": sample,
            }
        except Exception as exc:
            logger.exception("[HEALTH][POLYMARKET] Failed: %s", exc)
            return {
                "ok": False,
                "open_markets": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def run_checks(self) -> dict:
        started = datetime.now(timezone.utc)
        llm = await self.check_llm()
        polymarket = await self.check_polymarket()
        ok = bool(llm.get("ok")) and bool(polymarket.get("ok"))

        result = {
            "ok": ok,
            "checked_at": started.isoformat(),
            "llm": llm,
            "polymarket": polymarket,
        }
        if ok:
            logger.info("[HEALTH] dependencies OK")
        else:
            logger.warning("[HEALTH] dependencies degraded: %s", result)

        # Алерты в Info-бот с раздельным троттлингом: LLM и Polymarket.
        if not llm.get("ok"):
            await self._maybe_alert_issue("llm", self._format_llm_alert(llm))
        if not polymarket.get("ok"):
            await self._maybe_alert_issue("polymarket", self._format_polymarket_alert(polymarket))

        return result

    async def _maybe_alert_issue(self, issue_key: str, text: str) -> None:
        now = monotonic()
        last = self._last_alert_ts.get(issue_key, 0.0)
        if (now - last) < _ALERT_INTERVAL_SECONDS:
            logger.debug(
                "[HEALTH][ALERT] skip throttled issue=%s remaining_sec=%.0f",
                issue_key,
                _ALERT_INTERVAL_SECONDS - (now - last),
            )
            return

        sent = await self._send_info_alert(text)
        if sent:
            self._last_alert_ts[issue_key] = now

    async def _send_info_alert(self, text: str) -> bool:
        runtime = await self.runtime_config.snapshot()
        token = runtime.telegram_bot_token
        chat_id = runtime.telegram_chat_id
        if not token or not chat_id:
            logger.warning("[HEALTH][ALERT] Info bot token/chat_id not configured.")
            return False

        payload = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            logger.warning("[HEALTH][ALERT] sent to Info-bot")
            return True
        except Exception as exc:
            logger.exception("[HEALTH][ALERT] failed to send message: %s", exc)
            return False

    @staticmethod
    def _format_llm_alert(llm: dict) -> str:
        models = llm.get("models") or []
        failed = [m for m in models if not m.get("ok")]
        if failed:
            details = "; ".join(
                f"{m.get('name', 'unknown')}: {m.get('error', 'error')}" for m in failed[:3]
            )
        else:
            details = llm.get("message", "LLM check failed")

        return "\n".join(
            [
                "🚨 ПРОБЛЕМА С LLM",
                "Проверка LLM не пройдена.",
                f"Детали: {details[:500]}",
                "Повторный алерт по этой проблеме: через 30 минут, если не восстановится.",
            ]
        )

    @staticmethod
    def _format_polymarket_alert(polymarket: dict) -> str:
        details = polymarket.get("error") or f"open_markets={polymarket.get('open_markets', 0)}"
        return "\n".join(
            [
                "🚨 ПРОБЛЕМА С POLYMARKET",
                "Проверка Polymarket не пройдена.",
                f"Детали: {str(details)[:500]}",
                "Повторный алерт по этой проблеме: через 30 минут, если не восстановится.",
            ]
        )
