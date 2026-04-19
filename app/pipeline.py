import logging

from app.config import Settings
from app.services.analysis import AnalysisService
from app.services.decision import DecisionService
from app.services.execution import ExecutionService
from app.services.ingestion import IngestionService
from app.services.notifier import TelegramNotifier
from app.services.storage import Storage

logger = logging.getLogger(__name__)


class TradingPipeline:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        ingestion: IngestionService,
        analysis: AnalysisService,
        decision: DecisionService,
        execution: ExecutionService,
        notifier: TelegramNotifier,
    ):
        self.settings = settings
        self.storage = storage
        self.ingestion = ingestion
        self.analysis = analysis
        self.decision = decision
        self.execution = execution
        self.notifier = notifier

    async def run_once(self) -> dict:
        packets = await self.ingestion.collect_event_packets(self.settings.max_events_per_cycle)
        processed = 0
        for packet in packets:
            try:
                analysis = await self.analysis.analyze(packet)
                decision = await self.decision.decide(analysis)
                execution = await self.execution.execute(decision)

                await self.storage.store_decision(packet, analysis, decision, execution)
                await self.storage.mark_processed(
                    packet.world_event.url,
                    packet.world_event.title,
                    packet.world_event.ingested_at.isoformat(),
                )
                await self.notifier.notify(analysis, decision, execution)
                processed += 1
            except Exception as exc:
                logger.exception("Failed to process event packet: %s", exc)
        return {"fetched_packets": len(packets), "processed_packets": processed}
