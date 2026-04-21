from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from app.config import LLMConfig, Settings
from app.services.storage import Storage


def _to_bool(raw: str | bool | None, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _to_float(raw: str | float | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _to_int(raw: str | int | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass
class RuntimeSnapshot:
    dry_run: bool
    auto_execute: bool
    max_bet_usd: float
    min_confidence: float
    max_daily_loss_usd: float
    min_market_volume: float
    initial_bankroll_usd: float
    user_language: str
    telegram_bot_token: str
    telegram_chat_id: str
    polymarket_clob_host: str
    polymarket_chain_id: int
    polymarket_signature_type: int
    polymarket_private_key: str
    polymarket_funder_address: str
    llms: list[LLMConfig]


class RuntimeConfigService:
    def __init__(self, settings: Settings, storage: Storage, cache_ttl_seconds: int = 15):
        self.settings = settings
        self.storage = storage
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_map: dict[str, str] = {}
        self._cached_until = 0.0

    async def overrides(self) -> dict[str, str]:
        now = monotonic()
        if now < self._cached_until:
            return self._cached_map
        self._cached_map = await self.storage.get_runtime_config_map()
        self._cached_until = now + self.cache_ttl_seconds
        return self._cached_map

    async def snapshot(self) -> RuntimeSnapshot:
        o = await self.overrides()

        llms = self._build_llm_configs(o)
        return RuntimeSnapshot(
            dry_run=_to_bool(o.get("DRY_RUN"), self.settings.dry_run),
            auto_execute=_to_bool(o.get("AUTO_EXECUTE"), self.settings.auto_execute),
            max_bet_usd=_to_float(o.get("MAX_BET_USD"), self.settings.max_bet_usd),
            min_confidence=_to_float(o.get("MIN_CONFIDENCE"), self.settings.min_confidence),
            max_daily_loss_usd=_to_float(o.get("MAX_DAILY_LOSS_USD"), self.settings.max_daily_loss_usd),
            min_market_volume=_to_float(o.get("MIN_MARKET_VOLUME"), self.settings.min_market_volume),
            initial_bankroll_usd=_to_float(
                o.get("INITIAL_BANKROLL_USD"), self.settings.initial_bankroll_usd
            ),
            user_language=str(o.get("USER_LANGUAGE", self.settings.user_language)).strip().lower(),
            telegram_bot_token=o.get("TELEGRAM_BOT_TOKEN", self.settings.telegram_bot_token),
            telegram_chat_id=o.get("TELEGRAM_CHAT_ID", self.settings.telegram_chat_id),
            polymarket_clob_host=o.get("POLYMARKET_CLOB_HOST", self.settings.polymarket_clob_host),
            polymarket_chain_id=_to_int(o.get("POLYMARKET_CHAIN_ID"), self.settings.polymarket_chain_id),
            polymarket_signature_type=_to_int(
                o.get("POLYMARKET_SIGNATURE_TYPE"), self.settings.polymarket_signature_type
            ),
            polymarket_private_key=o.get("POLYMARKET_PRIVATE_KEY", self.settings.polymarket_private_key),
            polymarket_funder_address=o.get(
                "POLYMARKET_FUNDER_ADDRESS", self.settings.polymarket_funder_address
            ),
            llms=llms,
        )

    async def set_value(self, key: str, value: str, is_secret: bool = False) -> None:
        await self.storage.set_runtime_config(key=key, value=value, is_secret=is_secret)
        self._cached_until = 0.0

    async def list_values(self) -> list[tuple[str, bool, str]]:
        return await self.storage.list_runtime_config()

    def _build_llm_configs(self, o: dict[str, str]) -> list[LLMConfig]:
        llms: list[LLMConfig] = []
        cfg = LLMConfig(
            name=o.get("LLM_1_NAME", self.settings.llm_1_name),
            base_url=o.get("LLM_1_BASE_URL", self.settings.llm_1_base_url),
            model=o.get("LLM_1_MODEL", self.settings.llm_1_model),
            api_key=o.get("LLM_1_API_KEY", self.settings.llm_1_api_key),
        )
        if cfg.enabled:
            llms.append(cfg)
        return llms
