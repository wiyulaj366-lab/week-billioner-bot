import logging

from app.config import Settings
from app.services.analysis import AnalysisService
from app.services.decision import DecisionService
from app.services.execution import ExecutionService
from app.services.ingestion import IngestionService
from app.services.notifier import TelegramNotifier
from app.services.runtime_config import RuntimeConfigService
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
        runtime_config: RuntimeConfigService,
    ):
        self.settings = settings
        self.storage = storage
        self.ingestion = ingestion
        self.analysis = analysis
        self.decision = decision
        self.execution = execution
        self.notifier = notifier
        self.runtime_config = runtime_config

    async def run_once(self) -> dict:
        packets = await self.ingestion.collect_event_packets(self.settings.max_events_per_cycle)
        processed = 0
        signaled = 0
        for packet in packets:
            try:
                analysis = await self.analysis.analyze(packet)
                decision = await self.decision.decide(analysis)
                runtime = await self.runtime_config.snapshot()
                require_confirmation = not runtime.auto_execute

                decision_state = "skipped"
                execution = await self.execution.execute(decision)
                if decision.action != "SKIP":
                    if require_confirmation:
                        decision_state = "pending_approval"
                        execution = execution.model_copy(
                            update={
                                "success": True,
                                "message": "Ожидает подтверждения в админ-панели.",
                            }
                        )
                    else:
                        decision_state = "executed"

                decision_id = await self.storage.store_decision(
                    packet, analysis, decision, execution, decision_state=decision_state
                )
                await self.storage.mark_processed(
                    packet.world_event.url,
                    packet.world_event.title,
                    packet.world_event.ingested_at.isoformat(),
                )
                if decision.action != "SKIP":
                    await self.notifier.notify_actionable(
                        analysis=analysis,
                        decision=decision,
                        execution=execution,
                        decision_id=decision_id,
                        require_confirmation=require_confirmation,
                    )
                    signaled += 1
                processed += 1
            except Exception as exc:
                logger.exception("Failed to process event packet: %s", exc)
        return {
            "fetched_packets": len(packets),
            "processed_packets": processed,
            "signaled_packets": signaled,
        }
