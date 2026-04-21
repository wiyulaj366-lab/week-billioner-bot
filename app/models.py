from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class WorldEvent(BaseModel):
    source: str
    title: str
    summary: str
    url: str
    published_at: Optional[datetime] = None
    ingested_at: datetime = Field(default_factory=utc_now)


class PolymarketMarket(BaseModel):
    market_id: str
    question: str
    url: Optional[str] = None
    volume_usd: float = 0.0
    liquidity_usd: float = 0.0
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    end_date: Optional[datetime] = None


class EventPacket(BaseModel):
    world_event: WorldEvent
    candidate_markets: list[PolymarketMarket]
    priority_score: float = 0.0
    priority_reason: str = ""


class ModelAnalysis(BaseModel):
    model_name: str
    thesis: str
    probability_shift: float = 0.0
    confidence: float = 0.0
    risks: list[str] = Field(default_factory=list)
    recommended_side: Literal["YES", "NO", "SKIP"] = "SKIP"
    time_horizon_hours: int = 24


class AggregatedAnalysis(BaseModel):
    packet: EventPacket
    model_outputs: list[ModelAnalysis]
    consensus_side: Literal["YES", "NO", "SKIP"]
    consensus_confidence: float
    summary_reasoning: str


class Decision(BaseModel):
    market: Optional[PolymarketMarket] = None
    action: Literal["BET_YES", "BET_NO", "SKIP"] = "SKIP"
    stake_usd: float = 0.0
    confidence: float = 0.0
    rationale: str
    blocked_by_guardrail: bool = False
    guardrail_reason: Optional[str] = None


class ExecutionResult(BaseModel):
    simulated: bool = True
    success: bool = False
    order_id: Optional[str] = None
    message: str


DecisionState = Literal[
    "skipped",
    "pending_approval",
    "rejected",
    "executed",
    "settled_win",
    "settled_loss",
]


class PortfolioStats(BaseModel):
    initial_bankroll_usd: float
    current_bankroll_usd: float
    pnl_usd: float
    total_bets: int
    wins: int
    losses: int
    win_rate: float
    pending_approval: int
    open_positions: int
