"""
Append-only performance log for Agent of Sats.

Implements a Hypercore-like interface backed by SQLite.
Every trade, PnL snapshot, and system event is stored as an immutable,
monotonically-ordered event.  When we migrate to real Hypercore the
callers won't need to change – only the storage backend swaps.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

logger = logging.getLogger("agent_of_sats.store")

# ── Event types ─────────────────────────────────────────────────────────────

EVENT_TRADE_OPEN = "trade_open"
EVENT_TRADE_CLOSE = "trade_close"
EVENT_PNL_SNAPSHOT = "pnl_snapshot"
EVENT_STRATEGY_DECISION = "strategy_decision"
EVENT_ERROR = "error"

# ── Abstract interface (what callers see) ───────────────────────────────────


class PerformanceLog:
    """
    Hypercore-compatible append-only event log.

    Public API:
        append_event(event)     – persist a structured event
        get_latest_snapshot()   – most recent pnl_snapshot event
        iter_events(limit)      – async iterator over recent events
        get_events_since(ts)    – events after a unix timestamp
        compute_pnl_summary()   – derive a PnL summary from the log
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.getenv(
            "PERFORMANCE_LOG_DB", "data/performance_log.db"
        )
        self._db: aiosqlite.Connection | None = None

    # ── lifecycle ───────────────────────────────────────────────────────

    async def open(self) -> None:
        """Open the database and ensure the schema exists."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL    NOT NULL,
                type     TEXT    NOT NULL,
                payload  TEXT    NOT NULL
            )
            """
        )
        await self._db.commit()
        logger.info("Performance log opened at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── writes ──────────────────────────────────────────────────────────

    async def append_event(self, event: dict[str, Any]) -> int:
        """
        Append a structured event.

        *event* must contain at least ``type`` and ``payload`` keys.
        A ``ts`` (unix epoch float) is added automatically if missing.

        Returns the sequence number of the new event.
        """
        assert self._db is not None, "Log not open – call .open() first"
        ts = event.get("ts", time.time())
        etype = event["type"]
        payload = json.dumps(event.get("payload", {}))
        cursor = await self._db.execute(
            "INSERT INTO events (ts, type, payload) VALUES (?, ?, ?)",
            (ts, etype, payload),
        )
        await self._db.commit()
        seq = cursor.lastrowid
        logger.debug("Appended event seq=%s type=%s", seq, etype)
        return seq  # type: ignore[return-value]

    # ── reads ───────────────────────────────────────────────────────────

    async def get_latest_snapshot(self) -> dict[str, Any] | None:
        """Return the most recent ``pnl_snapshot`` event, or None."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT seq, ts, type, payload FROM events "
            "WHERE type = ? ORDER BY seq DESC LIMIT 1",
            (EVENT_PNL_SNAPSHOT,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_event(row)

    async def iter_events(
        self, limit: int | None = None, event_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Return recent events, newest first."""
        assert self._db is not None
        query = "SELECT seq, ts, type, payload FROM events"
        params: list[Any] = []
        if event_type:
            query += " WHERE type = ?"
            params.append(event_type)
        query += " ORDER BY seq DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_event(r) for r in rows]

    async def get_events_since(self, since_ts: float) -> list[dict[str, Any]]:
        """Return all events with ts >= *since_ts*, oldest first."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT seq, ts, type, payload FROM events WHERE ts >= ? ORDER BY seq ASC",
            (since_ts,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_event(r) for r in rows]

    # ── derived analytics ───────────────────────────────────────────────

    async def compute_pnl_summary(
        self,
        window_hours: float = 24.0,
    ) -> dict[str, Any]:
        """
        Derive a PnL summary from trade_close events in the given window.

        Returns cumulative_pnl, realized_pnl_window, max_drawdown, and the
        window duration.
        """
        assert self._db is not None

        # All trade_close events
        all_closes = await self.iter_events(event_type=EVENT_TRADE_CLOSE)
        all_closes.reverse()  # oldest first

        cumulative_pnl = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for ev in all_closes:
            pnl = ev["payload"].get("realized_pnl", 0.0)
            cumulative_pnl += pnl
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_drawdown:
                max_drawdown = dd

        # Windowed PnL
        cutoff = time.time() - window_hours * 3600
        window_closes = [e for e in all_closes if e["ts"] >= cutoff]
        realized_window = sum(
            e["payload"].get("realized_pnl", 0.0) for e in window_closes
        )

        return {
            "cumulative_pnl_usd": round(cumulative_pnl, 4),
            "realized_pnl_window_usd": round(realized_window, 4),
            "window_hours": window_hours,
            "max_drawdown_usd": round(max_drawdown, 4),
            "total_closed_trades": len(all_closes),
        }


# ── helpers ─────────────────────────────────────────────────────────────────


def _row_to_event(row: tuple) -> dict[str, Any]:
    seq, ts, etype, payload_str = row
    return {
        "seq": seq,
        "ts": ts,
        "type": etype,
        "payload": json.loads(payload_str),
        "iso": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
    }
