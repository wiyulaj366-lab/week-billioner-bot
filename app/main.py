import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.config import get_settings
from app.log import configure_logging
from app.pipeline import TradingPipeline
from app.services.admin_bot import TelegramAdminBot
from app.services.analysis import AnalysisService
from app.services.decision import DecisionService
from app.services.execution import ExecutionService
from app.services.ingestion import IngestionService
from app.services.notifier import TelegramNotifier
from app.services.polymarket import PolymarketClient
from app.services.runtime_config import RuntimeConfigService
from app.services.storage import Storage
from app.services.world_events import WorldEventsClient

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="week-billioner-bot", version="0.1.0")
scheduler = AsyncIOScheduler(timezone="UTC")

pipeline: Optional[TradingPipeline] = None
admin_bot: Optional[TelegramAdminBot] = None
last_run_at: Optional[datetime] = None
last_run_result: dict = {}
is_running = False


async def _run_pipeline_job() -> None:
    global last_run_at, last_run_result, is_running
    if is_running:
        logger.warning("Previous cycle is still running; skip this tick.")
        return
    is_running = True
    try:
        assert pipeline is not None
        result = await pipeline.run_once()
        last_run_result = result
        last_run_at = datetime.now(timezone.utc)
        logger.info("Cycle complete: %s", result)
    finally:
        is_running = False


async def _poll_admin_bot_job() -> None:
    if admin_bot is None:
        return
    await admin_bot.poll_once()


@app.on_event("startup")
async def on_startup() -> None:
    global pipeline, admin_bot
    storage = Storage(settings.database_path)
    await storage.init()
    runtime_config = RuntimeConfigService(settings=settings, storage=storage)

    ingestion = IngestionService(
        world_client=WorldEventsClient(settings.get_world_feed_list()),
        polymarket_client=PolymarketClient(settings.polymarket_events_url),
        storage=storage,
    )
    analysis = AnalysisService(runtime_config)
    decision = DecisionService(settings, storage, runtime_config)
    execution = ExecutionService(settings, runtime_config)
    notifier = TelegramNotifier(settings, runtime_config)
    admin_bot = TelegramAdminBot(
        settings=settings,
        runtime_config=runtime_config,
        storage=storage,
        execution=execution,
    )

    pipeline = TradingPipeline(
        settings=settings,
        storage=storage,
        ingestion=ingestion,
        analysis=analysis,
        decision=decision,
        execution=execution,
        notifier=notifier,
        runtime_config=runtime_config,
    )

    scheduler.add_job(_run_pipeline_job, "interval", seconds=settings.poll_interval_seconds, id="poll-cycle")
    if admin_bot.enabled():
        scheduler.add_job(_poll_admin_bot_job, "interval", seconds=5, id="admin-bot-poll")
        logger.info("Admin bot polling enabled.")
    scheduler.start()
    logger.info("Scheduler started with interval=%ss", settings.poll_interval_seconds)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "env": settings.app_env,
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
        "last_run_result": last_run_result,
        "scheduler_running": scheduler.running,
    }


@app.post("/run-once")
async def run_once() -> dict:
    await _run_pipeline_job()
    return {"ok": True, "last_run_at": last_run_at, "last_run_result": last_run_result}
