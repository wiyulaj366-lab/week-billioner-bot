from functools import lru_cache
from typing import List

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    database_path: str = "./data/bot.db"
    poll_interval_seconds: int = 180
    max_events_per_cycle: int = 15

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    admin_telegram_bot_token: str = ""
    admin_telegram_user_id: int = 0

    dry_run: bool = True
    auto_execute: bool = False
    max_bet_usd: float = 25.0
    min_confidence: float = 0.65
    max_daily_loss_usd: float = 100.0
    min_market_volume: float = 5000.0
    initial_bankroll_usd: float = 1000.0
    user_language: str = "ru"

    polymarket_events_url: str = "https://gamma-api.polymarket.com/events"
    world_feeds: str = (
        "https://feeds.bbci.co.uk/news/world/rss.xml,"
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml,"
        "https://www.aljazeera.com/xml/rss/all.xml,"
        "https://www.coindesk.com/arc/outboundfeeds/rss/,"
        "https://cointelegraph.com/rss,"
        "https://www.kyivpost.com/feed,"
        "https://www.theguardian.com/world/rss,"
        "https://www.reutersagency.com/feed/?best-topics=world&post_type=best,"
        "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"
    )

    llm_1_name: str = "ModelA"
    llm_1_base_url: str = ""
    llm_1_model: str = ""
    llm_1_api_key: str = ""

    llm_2_name: str = "ModelB"
    llm_2_base_url: str = ""
    llm_2_model: str = ""
    llm_2_api_key: str = ""

    llm_3_name: str = "ModelC"
    llm_3_base_url: str = ""
    llm_3_model: str = ""
    llm_3_api_key: str = ""

    def get_world_feed_list(self) -> List[str]:
        return [x.strip() for x in self.world_feeds.split(",") if x.strip()]

    def get_llm_configs(self) -> List[LLMConfig]:
        return [
            LLMConfig(
                name=self.llm_1_name,
                base_url=self.llm_1_base_url,
                model=self.llm_1_model,
                api_key=self.llm_1_api_key,
            ),
            LLMConfig(
                name=self.llm_2_name,
                base_url=self.llm_2_base_url,
                model=self.llm_2_model,
                api_key=self.llm_2_api_key,
            ),
            LLMConfig(
                name=self.llm_3_name,
                base_url=self.llm_3_base_url,
                model=self.llm_3_model,
                api_key=self.llm_3_api_key,
            ),
        ]

    def active_llm_configs(self) -> List[LLMConfig]:
        return [cfg for cfg in self.get_llm_configs() if cfg.enabled]

    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
