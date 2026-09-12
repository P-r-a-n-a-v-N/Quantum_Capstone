"""The in-process snapshot store -- the dashboard's single source of truth.

Every browser, every tab and every REST call reads from here. Nothing but the
poller writes to it. That is the whole reason one poll to IBM can serve any
number of viewers without multiplying our request rate.

A ``TTLCache`` is used rather than a plain attribute so that staleness is
*expressed in the data structure itself*: if the poller dies, the entry expires
and `/api/health` starts reporting an empty cache instead of silently serving a
snapshot from an hour ago.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cachetools import TTLCache

from ..models import TelemetrySnapshot

_SNAPSHOT_KEY = "current"


class TelemetryStore:
    """Holds the latest snapshot plus the poller's own health counters."""

    def __init__(self, ttl_seconds: float) -> None:
        # One slot; TTL is a multiple of the poll interval so a single missed
        # tick does not blank the dashboard, but a dead poller does surface.
        self._cache: TTLCache = TTLCache(maxsize=1, ttl=ttl_seconds)
        self.last_successful_poll: datetime | None = None
        self.consecutive_failures: int = 0
        self.poller_running: bool = False

    # -- writes (poller only) ------------------------------------------------
    def set_snapshot(self, snapshot: TelemetrySnapshot) -> None:
        self._cache[_SNAPSHOT_KEY] = snapshot

    def record_success(self) -> None:
        self.last_successful_poll = datetime.now(timezone.utc)
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    # -- reads (everyone) ----------------------------------------------------
    def get_snapshot(self) -> TelemetrySnapshot | None:
        return self._cache.get(_SNAPSHOT_KEY)

    @property
    def is_populated(self) -> bool:
        return _SNAPSHOT_KEY in self._cache
