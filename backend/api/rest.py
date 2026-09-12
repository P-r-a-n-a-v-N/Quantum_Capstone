"""REST surface.

Two rules hold for every endpoint in this module:

  1. It reads the cache. It never calls IBM. The poller alone does that.
  2. It answers even when the upstream is down, because the cache is always
     populated -- with live data when IBM is reachable, simulated data when not.

The initial page load uses `/api/telemetry`; after that the browser upgrades to
the WebSocket and these endpoints go quiet.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..models import (
    BackendRecommendation,
    HealthResponse,
    HistoryPoint,
    TelemetrySnapshot,
    TelemetrySource,
)
from ..core.analytics import recommend_backends

router = APIRouter(prefix="/api", tags=["telemetry"])


def _snapshot_or_503(request: Request) -> TelemetrySnapshot:
    snapshot = request.app.state.store.get_snapshot()
    if snapshot is None:
        # Only reachable if the poller has died and the cache TTL has lapsed --
        # which is precisely when a 503 is the honest answer.
        raise HTTPException(
            status_code=503,
            detail="Telemetry cache is empty; the background poller is not running.",
        )
    return snapshot


@router.get("/telemetry", response_model=TelemetrySnapshot)
async def get_telemetry(request: Request) -> TelemetrySnapshot:
    """Current fleet snapshot -- used for the initial paint before the WebSocket opens."""
    return _snapshot_or_503(request)


@router.get("/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    """Liveness plus an honest report of which mode the dashboard is in."""
    state = request.app.state
    snapshot = state.store.get_snapshot()
    return HealthResponse(
        status="ok" if state.store.poller_running else "degraded",
        source=snapshot.source if snapshot else TelemetrySource.MOCK,
        live_mode_configured=state.settings.live_mode_possible,
        poller_running=state.store.poller_running,
        last_successful_poll=state.store.last_successful_poll,
        consecutive_failures=state.store.consecutive_failures,
        connected_websocket_clients=state.connections.client_count,
        history_enabled=state.history is not None,
    )


@router.get("/recommendations", response_model=list[BackendRecommendation])
async def get_recommendations(
    request: Request,
    min_qubits: int = Query(0, ge=0, le=1000, description="Only consider devices this large."),
    limit: int = Query(3, ge=1, le=10),
    include_simulators: bool = Query(False),
) -> list[BackendRecommendation]:
    """Rank the best QPUs to submit to right now, filtered to your circuit size."""
    snapshot = _snapshot_or_503(request)
    return recommend_backends(
        snapshot.backends,
        limit=limit,
        min_qubits=min_qubits,
        include_simulators=include_simulators,
    )


@router.get("/history", response_model=list[HistoryPoint])
async def get_history(
    request: Request,
    minutes: int = Query(60, ge=1, le=10_080, description="Look-back window in minutes."),
    backend: str | None = Query(None, description="Restrict to a single backend."),
) -> list[HistoryPoint]:
    """Persisted queue-depth observations, so the chart is populated on first paint."""
    history = request.app.state.history
    if history is None:
        raise HTTPException(status_code=404, detail="History is disabled (ENABLE_HISTORY=false).")
    return await history.recent(minutes=minutes, backend=backend)


@router.get("/history/busiest")
async def get_busiest(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(5, ge=1, le=20),
) -> list[dict]:
    """Average and peak queue depth per backend over a window."""
    history = request.app.state.history
    if history is None:
        raise HTTPException(status_code=404, detail="History is disabled (ENABLE_HISTORY=false).")
    return await history.busiest_backends(hours=hours, limit=limit)
