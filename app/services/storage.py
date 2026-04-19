import json
import os
from datetime import date
from typing import Optional

import aiosqlite

from app.models import AggregatedAnalysis, Decision, EventPacket, ExecutionResult


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_url TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_title TEXT NOT NULL,
                    market_id TEXT,
                    action TEXT NOT NULL,
                    stake_usd REAL NOT NULL,
                    confidence REAL NOT NULL,
                    blocked_by_guardrail INTEGER NOT NULL,
                    rationale TEXT NOT NULL,
                    execution_success INTEGER NOT NULL,
                    execution_message TEXT NOT NULL,
                    raw_analysis TEXT NOT NULL
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS pnl_daily (
                    day TEXT PRIMARY KEY,
                    pnl_usd REAL NOT NULL
                );
                """
            )
            await db.commit()

    async def is_processed(self, event_url: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT 1 FROM processed_events WHERE event_url = ? LIMIT 1",
                (event_url,),
            )
            row = await cur.fetchone()
            return row is not None

    async def mark_processed(self, event_url: str, title: str, ingested_at: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO processed_events (event_url, title, ingested_at)
                VALUES (?, ?, ?)
                """,
                (event_url, title, ingested_at),
            )
            await db.commit()

    async def store_decision(
        self,
        packet: EventPacket,
        analysis: AggregatedAnalysis,
        decision: Decision,
        execution: ExecutionResult,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO decisions (
                    created_at, event_title, market_id, action, stake_usd, confidence,
                    blocked_by_guardrail, rationale, execution_success, execution_message, raw_analysis
                )
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet.world_event.title,
                    decision.market.market_id if decision.market else None,
                    decision.action,
                    decision.stake_usd,
                    decision.confidence,
                    int(decision.blocked_by_guardrail),
                    decision.rationale,
                    int(execution.success),
                    execution.message,
                    json.dumps(analysis.model_dump(mode="json"), ensure_ascii=True),
                ),
            )
            await db.commit()

    async def daily_pnl(self, day: Optional[date] = None) -> float:
        day = day or date.today()
        key = day.isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT pnl_usd FROM pnl_daily WHERE day = ? LIMIT 1", (key,))
            row = await cur.fetchone()
            return float(row[0]) if row else 0.0

    async def increment_daily_pnl(self, delta_usd: float, day: Optional[date] = None) -> None:
        day = day or date.today()
        key = day.isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO pnl_daily (day, pnl_usd) VALUES (?, ?)
                ON CONFLICT(day) DO UPDATE SET pnl_usd = pnl_usd + excluded.pnl_usd
                """,
                (key, delta_usd),
            )
            await db.commit()
