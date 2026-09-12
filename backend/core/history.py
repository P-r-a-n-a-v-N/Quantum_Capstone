"""Optional queue-depth history, persisted to a single SQLite file.

Why persist at all on a dashboard that is otherwise stateless? One concrete
reason: without it the queue chart is **empty for the first minute after every
page load**, because the client has to accumulate points from the live stream.
With ~40 lines of SQLite the chart is populated the instant the page opens, and
multi-day trend questions ("which QPU is busiest this week?") become answerable.

SQLite -- not Postgres -- because this is a single-writer, single-process
workload. A database server here would be architecture for its own sake.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from ..models import HistoryPoint, QPUBackend

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    backend      TEXT    NOT NULL,
    queue_length INTEGER NOT NULL,
    recorded_at  TEXT    NOT NULL
);
-- The dashboard only ever asks "recent points, newest first", so one composite
-- index on (recorded_at, backend) serves both the chart query and the pruner.
CREATE INDEX IF NOT EXISTS idx_queue_history_time
    ON queue_history (recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_history_backend_time
    ON queue_history (backend, recorded_at DESC);
"""


class HistoryStore:
    """Append-only queue-depth log with bounded retention."""

    def __init__(self, db_path: Path, retention_days: int = 7) -> None:
        self._db_path = db_path
        self._retention_days = retention_days
        self._ready = False

    async def initialise(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._ready = True
        logger.info("History store ready at %s", self._db_path)

    async def record(self, backends: list[QPUBackend]) -> None:
        """Persist one observation per backend for this tick."""
        if not self._ready or not backends:
            return
        recorded_at = datetime.now(timezone.utc).isoformat()
        rows = [(b.name, b.queue_length, recorded_at) for b in backends]
        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.executemany(
                    "INSERT INTO queue_history (backend, queue_length, recorded_at) "
                    "VALUES (?, ?, ?)",
                    rows,
                )
                await db.commit()
        except Exception:
            # History is a nice-to-have. It must never be able to take the
            # poller down, so this is swallowed after logging.
            logger.exception("Failed to record queue history; continuing.")

    async def recent(self, minutes: int = 60, backend: str | None = None) -> list[HistoryPoint]:
        """Return observations from the last `minutes`, oldest first."""
        if not self._ready:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        query = (
            "SELECT backend, queue_length, recorded_at FROM queue_history "
            "WHERE recorded_at >= ?"
        )
        params: list[object] = [cutoff]
        if backend:
            query += " AND backend = ?"
            params.append(backend)
        query += " ORDER BY recorded_at ASC"

        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
        except Exception:
            logger.exception("Failed to read queue history.")
            return []

        return [
            HistoryPoint(
                backend=row[0],
                queue_length=row[1],
                recorded_at=datetime.fromisoformat(row[2]),
            )
            for row in rows
        ]

    async def busiest_backends(self, hours: int = 24, limit: int = 5) -> list[dict]:
        """Average queue depth per backend over a window -- the 'busiest' view."""
        if not self._ready:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    "SELECT backend, AVG(queue_length) AS avg_q, MAX(queue_length) AS peak_q, "
                    "COUNT(*) AS samples FROM queue_history WHERE recorded_at >= ? "
                    "GROUP BY backend ORDER BY avg_q DESC LIMIT ?",
                    (cutoff, limit),
                ) as cursor:
                    rows = await cursor.fetchall()
        except Exception:
            logger.exception("Failed to aggregate queue history.")
            return []

        return [
            {
                "backend": row[0],
                "average_queue_length": round(row[1], 1),
                "peak_queue_length": row[2],
                "samples": row[3],
            }
            for row in rows
        ]

    async def latest_depths(self) -> dict[str, int]:
        """Most recent queue depth per backend, used to resume after a restart.

        Without this the simulator re-bootstraps with fresh random queues on
        every restart, and the chart shows a cliff where persisted history meets
        the new process. Resuming from the last known depths makes the series
        continuous across restarts.
        """
        if not self._ready:
            return {}
        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(
                    "SELECT backend, queue_length FROM queue_history "
                    "WHERE id IN (SELECT MAX(id) FROM queue_history GROUP BY backend)"
                ) as cursor:
                    rows = await cursor.fetchall()
        except Exception:
            logger.exception("Failed to read latest queue depths.")
            return {}
        return {row[0]: row[1] for row in rows}

    async def prune(self) -> int:
        """Drop observations past the retention window. Returns rows removed."""
        if not self._ready:
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        ).isoformat()
        try:
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute(
                    "DELETE FROM queue_history WHERE recorded_at < ?", (cutoff,)
                )
                await db.commit()
                return cursor.rowcount or 0
        except Exception:
            logger.exception("Failed to prune queue history.")
            return 0
