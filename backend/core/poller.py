"""The single background heartbeat.

Exactly one instance of this runs per process. It is the *only* thing in the
system that talks to IBM. Every browser, tab and REST caller reads the snapshot
it leaves behind in the cache.

That is the central architectural decision of this project. The naive design --
each connected client fetching from IBM -- multiplies our request rate by the
number of viewers and gets the account rate-limited by the third open tab. Here,
request rate is a constant: one poll per `POLL_INTERVAL_SECONDS`, forever,
whether nobody or a hundred people are watching.

The second decision lives in :meth:`_build_snapshot`: **this coroutine does not
raise.** Any failure -- expired token, DNS blip, IBM maintenance, rate limit --
is caught, converted into simulated telemetry, and stamped ``source="mock"``
with a human-readable `degraded_reason`. The dashboard therefore has no error
state and no blank state; it degrades honestly and recovers by itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..config import Settings
from ..mock_service import MockTelemetryService
from ..models import TelemetrySnapshot, TelemetrySource
from .analytics import build_summary, recommend_backends
from .cache import TelemetryStore
from .history import HistoryStore
from .ibm_client import IBMAuthError, IBMQuantumClient, IBMQuantumError

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[TelemetrySnapshot], Awaitable[None]]

# Back off when IBM is unhappy, but never so far that recovery is slow.
_MAX_BACKOFF_MULTIPLIER = 5
# Prune the history table roughly hourly rather than on every tick.
_PRUNE_EVERY_N_TICKS = 300


class TelemetryPoller:
    """Owns the poll loop, the fallback decision and cache writes."""

    def __init__(
        self,
        settings: Settings,
        store: TelemetryStore,
        history: HistoryStore | None = None,
        ibm_client: IBMQuantumClient | None = None,
        broadcast: BroadcastFn | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._history = history
        self._broadcast = broadcast
        self._mock = MockTelemetryService()
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._tick_count = 0

        # Only construct a live client when live mode is actually possible, so a
        # credential-free install never even builds an HTTP pool it won't use.
        if ibm_client is not None:
            self._client: IBMQuantumClient | None = ibm_client
        elif settings.live_mode_possible:
            self._client = IBMQuantumClient(settings)
        else:
            self._client = None

        # Latched on a hard auth failure: retrying a rejected API key every 12
        # seconds achieves nothing except noise in the logs.
        self._auth_failed_permanently = False
        # ...but the *reason* must survive, or every snapshot after the first
        # would report `degraded_reason=None` and the UI would silently switch
        # from "your credentials were rejected" to "no credentials configured".
        self._latched_reason: str | None = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()

        # Resume the simulator from the last persisted depths so the chart is
        # continuous across a restart rather than showing a cliff where stored
        # history meets a freshly randomised fleet.
        if self._history is not None:
            previous_depths = await self._history.latest_depths()
            if previous_depths:
                self._mock.seed_queue_depths(previous_depths)
                logger.info("Resumed simulator from %d stored queue depths.", len(previous_depths))

        # Produce one snapshot immediately so the very first HTTP request finds
        # a populated cache instead of a 503.
        await self.poll_once()
        self._task = asyncio.create_task(self._run(), name="telemetry-poller")
        self._store.poller_running = True
        logger.info(
            "Poller started in %s mode, interval %.1fs.",
            "LIVE" if self._client and not self._auth_failed_permanently else "MOCK",
            self._settings.poll_interval_seconds,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._store.poller_running = False
        if self._client is not None:
            await self._client.aclose()
        logger.info("Poller stopped.")

    # -- loop ----------------------------------------------------------------
    async def _run(self) -> None:
        while not self._stop_event.is_set():
            delay = self._next_delay()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return  # stop was requested during the wait
            except asyncio.TimeoutError:
                pass  # normal path: the interval elapsed

            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Belt and braces: poll_once already swallows everything, but a
                # bug in that swallowing must not kill the only poller we have.
                logger.exception("Unexpected error in poll loop; continuing.")

    @property
    def _will_attempt_live(self) -> bool:
        """Whether the next tick will actually reach out to IBM."""
        return self._client is not None and not self._auth_failed_permanently

    def _next_delay(self) -> float:
        """Linear backoff while IBM is failing, capped so recovery stays quick.

        Backoff exists to be kind to IBM. Once we have stopped calling IBM at
        all -- no credentials, or a latched rejection -- backing off is pure
        downside: it halves the dashboard's refresh rate to spare a service we
        are no longer touching.
        """
        if not self._will_attempt_live:
            return self._settings.poll_interval_seconds
        multiplier = min(self._store.consecutive_failures + 1, _MAX_BACKOFF_MULTIPLIER)
        return self._settings.poll_interval_seconds * multiplier

    # -- one tick ------------------------------------------------------------
    async def poll_once(self) -> TelemetrySnapshot:
        """Produce, store and broadcast exactly one snapshot. Never raises."""
        snapshot = await self._build_snapshot()
        self._store.set_snapshot(snapshot)
        self._tick_count += 1

        if self._history is not None:
            await self._history.record(snapshot.backends)
            if self._tick_count % _PRUNE_EVERY_N_TICKS == 0:
                removed = await self._history.prune()
                if removed:
                    logger.info("Pruned %d expired history rows.", removed)

        if self._broadcast is not None:
            try:
                await self._broadcast(snapshot)
            except Exception:
                logger.exception("Broadcast failed; snapshot is still cached.")

        return snapshot

    async def _build_snapshot(self) -> TelemetrySnapshot:
        """Try live, fall back to simulated. This is the graceful-degradation core."""
        if self._client is not None and not self._auth_failed_permanently:
            try:
                # Fetch both resources concurrently -- they are independent, and
                # serialising them would double the tick's latency for nothing.
                backends, jobs = await asyncio.gather(
                    self._client.fetch_backends(),
                    self._client.fetch_jobs(limit=50),
                )
                if not backends:
                    raise IBMQuantumError("IBM returned an empty backend list.")

                self._store.record_success()
                return self._assemble(
                    backends, jobs, TelemetrySource.LIVE, degraded_reason=None
                )

            except IBMAuthError as exc:
                # Credentials are wrong, not flaky. Stop hammering IAM and stay
                # in mock mode until the process is restarted with a fixed key.
                self._auth_failed_permanently = True
                self._store.record_failure()
                reason = f"IBM authentication failed: {exc}"
                self._latched_reason = reason
                logger.error("%s -- staying in simulated mode.", reason)
                return self._mock_snapshot(reason)

            except (IBMQuantumError, asyncio.TimeoutError) as exc:
                self._store.record_failure()
                reason = f"IBM unreachable: {exc}"
                logger.warning("%s -- serving simulated telemetry.", reason)
                return self._mock_snapshot(reason)

            except Exception as exc:  # pragma: no cover - defensive
                self._store.record_failure()
                reason = f"Unexpected error talking to IBM: {exc}"
                logger.exception(reason)
                return self._mock_snapshot(reason)

        # Either no credentials are configured / FORCE_MOCK_MODE is on -- the
        # documented default, which is not a failure and carries no reason -- or
        # we have latched a rejected credential, whose reason must keep being
        # reported for as long as it is true.
        return self._mock_snapshot(self._latched_reason)

    def _mock_snapshot(self, degraded_reason: str | None) -> TelemetrySnapshot:
        backends, jobs = self._mock.tick()
        return self._assemble(backends, jobs, TelemetrySource.MOCK, degraded_reason)

    def _assemble(
        self, backends, jobs, source: TelemetrySource, degraded_reason: str | None
    ) -> TelemetrySnapshot:
        # Operational machines first, then shortest queue -- the order an
        # operator actually wants to read the grid in.
        ordered = sorted(
            backends,
            key=lambda b: (not b.is_operational, b.is_simulator, b.queue_length),
        )
        return TelemetrySnapshot(
            source=source,
            backends=ordered,
            jobs=jobs,
            summary=build_summary(ordered, jobs),
            recommendations=recommend_backends(ordered, limit=3),
            degraded_reason=degraded_reason,
            poll_interval_seconds=self._settings.poll_interval_seconds,
            consecutive_failures=self._store.consecutive_failures,
            will_retry=not self._auth_failed_permanently,
        )
