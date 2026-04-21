import json
import os
from datetime import date
from typing import Optional

import aiosqlite

from app.models import (
    AggregatedAnalysis,
    Decision,
    DecisionState,
    EventPacket,
    ExecutionResult,
    PortfolioStats,
)


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
                    event_url TEXT,
                    event_title TEXT NOT NULL,
                    market_id TEXT,
                    market_question TEXT,
                    market_url TEXT,
                    action TEXT NOT NULL,
                    stake_usd REAL NOT NULL,
                    confidence REAL NOT NULL,
                    blocked_by_guardrail INTEGER NOT NULL,
                    rationale TEXT NOT NULL,
                    execution_success INTEGER NOT NULL,
                    execution_message TEXT NOT NULL,
                    decision_state TEXT NOT NULL DEFAULT 'pending_approval',
                    pnl_usd REAL NOT NULL DEFAULT 0,
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
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                """
            )
            await self._migrate_decisions_columns(db)
            await self._cleanup_pending_non_bet(db)
            await db.commit()

    async def _migrate_decisions_columns(self, db: aiosqlite.Connection) -> None:
        cur = await db.execute("PRAGMA table_info(decisions)")
        rows = await cur.fetchall()
        existing = {str(r[1]) for r in rows}
        required = {
            "event_url": "ALTER TABLE decisions ADD COLUMN event_url TEXT",
            "market_question": "ALTER TABLE decisions ADD COLUMN market_question TEXT",
            "market_url": "ALTER TABLE decisions ADD COLUMN market_url TEXT",
            "decision_state": "ALTER TABLE decisions ADD COLUMN decision_state TEXT NOT NULL DEFAULT 'pending_approval'",
            "pnl_usd": "ALTER TABLE decisions ADD COLUMN pnl_usd REAL NOT NULL DEFAULT 0",
        }
        for col, ddl in required.items():
            if col not in existing:
                await db.execute(ddl)

    async def _cleanup_pending_non_bet(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            UPDATE decisions
            SET decision_state = 'rejected',
                execution_success = 0,
                execution_message = 'Автоочистка: решение НЕ СТАВИТЬ не требует подтверждения.'
            WHERE decision_state = 'pending_approval'
              AND action IN ('SKIP', 'NO_BET')
            """
        )

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
        decision_state: DecisionState,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                INSERT INTO decisions (
                    created_at, event_url, event_title, market_id, market_question, market_url,
                    action, stake_usd, confidence, blocked_by_guardrail, rationale,
                    execution_success, execution_message, decision_state, pnl_usd, raw_analysis
                )
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    packet.world_event.url,
                    packet.world_event.title,
                    decision.market.market_id if decision.market else None,
                    decision.market.question if decision.market else None,
                    decision.market.url if decision.market else None,
                    decision.action,
                    decision.stake_usd,
                    decision.confidence,
                    int(decision.blocked_by_guardrail),
                    decision.rationale,
                    int(execution.success),
                    execution.message,
                    decision_state,
                    0.0,
                    json.dumps(analysis.model_dump(mode="json"), ensure_ascii=True),
                ),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_decision(self, decision_id: int) -> Optional[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM decisions WHERE id = ? LIMIT 1",
                (decision_id,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_decision_state(
        self,
        decision_id: int,
        decision_state: DecisionState,
        execution_success: Optional[bool] = None,
        execution_message: Optional[str] = None,
        pnl_delta: Optional[float] = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            sets = ["decision_state = ?"]
            params: list = [decision_state]
            if execution_success is not None:
                sets.append("execution_success = ?")
                params.append(int(execution_success))
            if execution_message is not None:
                sets.append("execution_message = ?")
                params.append(execution_message)
            if pnl_delta is not None:
                sets.append("pnl_usd = pnl_usd + ?")
                params.append(float(pnl_delta))
            params.append(decision_id)
            sql = f"UPDATE decisions SET {', '.join(sets)} WHERE id = ?"
            await db.execute(sql, tuple(params))
            await db.commit()
        if pnl_delta:
            await self.increment_daily_pnl(pnl_delta)

    async def list_pending_approvals(self, limit: int = 15) -> list[dict]:
                async with aiosqlite.connect(self.db_path) as db:
                        db.row_factory = aiosqlite.Row
                        cur = await db.execute(
                                """
                                SELECT id, created_at, event_title, event_url, market_question, market_url, action, stake_usd,
                                             confidence, decision_state, execution_message
                                FROM decisions
                                WHERE decision_state = 'pending_approval'
                                    AND action NOT IN ('SKIP', 'NO_BET')
                                ORDER BY id DESC
                                LIMIT ?
                                """,
                                (limit,),
                        )
                        return [dict(r) for r in await cur.fetchall()]

    async def list_open_positions(self, limit: int = 20) -> list[dict]:
        return await self._list_by_state(("executed",), limit=limit)

    async def list_recent_history(self, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT id, created_at, event_title, market_question, action, stake_usd, confidence,
                       decision_state, execution_message, pnl_usd
                FROM decisions
                WHERE decision_state IN ('rejected','settled_win','settled_loss')
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def _list_by_state(self, states: tuple[str, ...], limit: int) -> list[dict]:
        placeholders = ",".join("?" for _ in states)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"""
                  SELECT id, created_at, event_title, event_url, market_question, market_url, action, stake_usd, confidence,
                       decision_state, execution_message
                FROM decisions
                WHERE decision_state IN ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """,
                (*states, limit),
            )
            return [dict(r) for r in await cur.fetchall()]

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

    async def stats(self, initial_bankroll_usd: float) -> PortfolioStats:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT COUNT(*) FROM decisions WHERE action != 'NO_BET'")
            total_bets = int((await cur.fetchone())[0])

            cur = await db.execute("SELECT COUNT(*) FROM decisions WHERE decision_state = 'settled_win'")
            wins = int((await cur.fetchone())[0])

            cur = await db.execute("SELECT COUNT(*) FROM decisions WHERE decision_state = 'settled_loss'")
            losses = int((await cur.fetchone())[0])

            cur = await db.execute(
                """
                SELECT COUNT(*)
                FROM decisions
                WHERE decision_state = 'pending_approval'
                  AND action NOT IN ('SKIP', 'NO_BET')
                """
            )
            pending_approval = int((await cur.fetchone())[0])

            cur = await db.execute("SELECT COUNT(*) FROM decisions WHERE decision_state = 'executed'")
            open_positions = int((await cur.fetchone())[0])

            cur = await db.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM decisions")
            pnl = float((await cur.fetchone())[0] or 0.0)

        current = initial_bankroll_usd + pnl
        win_rate = (wins / (wins + losses)) if (wins + losses) else 0.0
        return PortfolioStats(
            initial_bankroll_usd=initial_bankroll_usd,
            current_bankroll_usd=current,
            pnl_usd=pnl,
            total_bets=total_bets,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            pending_approval=pending_approval,
            open_positions=open_positions,
        )

    async def set_runtime_config(self, key: str, value: str, is_secret: bool = False) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO runtime_config (key, value, is_secret, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    is_secret=excluded.is_secret,
                    updated_at=datetime('now')
                """,
                (key, value, int(is_secret)),
            )
            await db.commit()

    async def get_runtime_config_map(self) -> dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT key, value FROM runtime_config")
            rows = await cur.fetchall()
            return {str(k): str(v) for k, v in rows}

    async def list_runtime_config(self) -> list[tuple[str, bool, str]]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT key, is_secret, updated_at FROM runtime_config ORDER BY key ASC"
            )
            rows = await cur.fetchall()
            return [(str(k), bool(i), str(ts)) for k, i, ts in rows]
