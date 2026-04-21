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
from app.services.btc_pipeline import BtcPipeline
from app.services.btc_ticker import BtcTicker
from app.services.decision import DecisionService
from app.services.dependency_health import DependencyHealthService
from app.services.execution import ExecutionService
from app.services.ingestion import IngestionService
from app.services.notifier import TelegramNotifier
from app.services.polymarket import PolymarketClient
from app.services.position_monitor import PositionMonitor
from app.services.runtime_config import RuntimeConfigService
from app.services.storage import Storage
from app.services.world_events import WorldEventsClient

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title="week-billioner-bot", version="0.1.0")
scheduler = AsyncIOScheduler(timezone="UTC")

pipeline: Optional[TradingPipeline] = None
btc_pipeline: Optional[BtcPipeline] = None
position_monitor: Optional[PositionMonitor] = None
dependency_health: Optional[DependencyHealthService] = None
admin_bot: Optional[TelegramAdminBot] = None
last_run_at: Optional[datetime] = None
last_run_result: dict = {}
is_running = False
is_btc_running = False


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


async def _run_btc_pipeline_job() -> None:
    global is_btc_running
    if is_btc_running:
        logger.warning("BTC pipeline already running, skip.")
        return
    is_btc_running = True
    try:
        assert btc_pipeline is not None
        result = await btc_pipeline.run_once()
        logger.info("BTC cycle: %s", result)
    except Exception as exc:
        logger.exception("BTC pipeline error: %s", exc)
    finally:
        is_btc_running = False


async def _run_position_monitor_job() -> None:
    if position_monitor is None:
        return
    try:
        result = await position_monitor.run_once()
        if result.get("checked", 0) > 0:
            logger.info("Position monitor: %s", result)
    except Exception as exc:
        logger.exception("Position monitor error: %s", exc)


async def _run_dependency_health_job() -> None:
    if dependency_health is None:
        return
    try:
        result = await dependency_health.run_checks()
        logger.info("Dependency health: ok=%s", result.get("ok"))
    except Exception as exc:
        logger.exception("Dependency health job error: %s", exc)


async def _poll_admin_bot_job() -> None:
    if admin_bot is None:
        return
    await admin_bot.poll_once()


@app.on_event("startup")
async def on_startup() -> None:
    global pipeline, btc_pipeline, position_monitor, dependency_health, admin_bot
    storage = Storage(settings.database_path)
    await storage.init()
    runtime_config = RuntimeConfigService(settings=settings, storage=storage)
    polymarket_client = PolymarketClient(settings.polymarket_events_url)
    world_client = WorldEventsClient(settings.get_world_feed_list())

    ingestion = IngestionService(
        world_client=world_client,
        polymarket_client=polymarket_client,
        storage=storage,
    )
    analysis = AnalysisService(runtime_config)
    decision = DecisionService(settings, storage, runtime_config)
    execution = ExecutionService(settings, runtime_config, polymarket_client)
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

    btc_pipeline = BtcPipeline(
        settings=settings,
        storage=storage,
        ticker=BtcTicker(),
        world_client=world_client,
        polymarket_client=polymarket_client,
        execution=execution,
        notifier=notifier,
        runtime_config=runtime_config,
    )

    position_monitor = PositionMonitor(
        settings=settings,
        storage=storage,
        polymarket_client=polymarket_client,
        execution=execution,
        notifier=notifier,
        runtime_config=runtime_config,
    )

    dependency_health = DependencyHealthService(
        runtime_config=runtime_config,
        polymarket_client=polymarket_client,
    )

    # Первичная проверка зависимостей сразу на старте.
    startup_health = await dependency_health.run_checks()
    logger.info("Startup dependency health: %s", startup_health)

    scheduler.add_job(_run_pipeline_job, "interval", seconds=settings.poll_interval_seconds, id="poll-cycle")
    # BTC 5-мин рынки: цикл каждые 4 минуты (240 сек)
    scheduler.add_job(_run_btc_pipeline_job, "interval", seconds=240, id="btc-cycle")
    # Мониторинг позиций: каждые 2 минуты
    scheduler.add_job(_run_position_monitor_job, "interval", seconds=120, id="position-monitor")
    # Проверка внешних зависимостей: каждые 10 минут
    scheduler.add_job(_run_dependency_health_job, "interval", seconds=600, id="dependency-health")
    if admin_bot.enabled():
        scheduler.add_job(_poll_admin_bot_job, "interval", seconds=5, id="admin-bot-poll")
        logger.info("Admin bot polling enabled.")
    scheduler.start()
    logger.info(
        "Scheduler started: news=%ss, btc=240s, positions=120s",
        settings.poll_interval_seconds,
    )


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


@app.get("/health/dependencies")
async def health_dependencies() -> dict:
    if dependency_health is None:
        return {"ok": False, "error": "dependency_health not initialized"}
    return await dependency_health.run_checks()
