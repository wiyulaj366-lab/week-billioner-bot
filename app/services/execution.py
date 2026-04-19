from app.config import Settings
from app.models import Decision, ExecutionResult


class ExecutionService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def execute(self, decision: Decision) -> ExecutionResult:
        if decision.action == "SKIP":
            return ExecutionResult(simulated=True, success=True, message="No trade executed (SKIP).")

        if self.settings.dry_run or not self.settings.auto_execute:
            return ExecutionResult(
                simulated=True,
                success=True,
                order_id="simulated-order",
                message=f"DRY_RUN: would execute {decision.action} with ${decision.stake_usd:.2f}",
            )

        # Live execution adapter placeholder.
        # Add signed Polymarket CLOB order placement here when you are ready for production trading.
        return ExecutionResult(
            simulated=False,
            success=False,
            message="Live execution not implemented. Keep DRY_RUN=true until CLOB signer is added.",
        )
