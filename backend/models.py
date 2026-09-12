"""Typed domain model for the dashboard.

This module is the contract between IBM's REST payloads, the simulator, the
WebSocket stream and the React frontend. Both the live client and the mock
service emit these exact types, which is why the frontend cannot tell them
apart -- and why swapping one for the other during an outage is safe.

Field names here follow IBM's REST vocabulary where IBM has one, and our own
where IBM does not (see `estimated_wait_seconds`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, computed_field


# -----------------------------------------------------------------------------
# Enumerations
# -----------------------------------------------------------------------------
class BackendStatus(str, Enum):
    """Operational state of a QPU.

    IBM returns these inside a nested object as ``{"name": "online", ...}``
    with the values below. We flatten that object but keep IBM's vocabulary.
    """

    ONLINE = "online"
    PAUSED = "paused"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> "BackendStatus":
        if not raw:
            return cls.UNKNOWN
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_accepting_jobs(self) -> bool:
        return self is BackendStatus.ONLINE


class JobStatus(str, Enum):
    """Lifecycle state of a Runtime job.

    IBM returns title-case values (``Queued``, ``Running``, ``Completed``,
    ``Cancelled``, ``Failed``). We normalise to upper snake so the frontend
    has a single stable vocabulary to colour-code against.
    """

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def parse(cls, raw: str | None) -> "JobStatus":
        if not raw:
            return cls.UNKNOWN
        normalised = raw.strip().upper()
        # IBM has used a few synonyms across API versions; fold them in.
        aliases = {
            "DONE": cls.COMPLETED,
            "COMPLETE": cls.COMPLETED,
            "ERROR": cls.FAILED,
            "CANCELED": cls.CANCELLED,
            "PENDING": cls.QUEUED,
            "INITIALIZING": cls.RUNNING,
            "VALIDATING": cls.QUEUED,
        }
        if normalised in aliases:
            return aliases[normalised]
        try:
            return cls(normalised)
        except ValueError:
            return cls.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.FAILED)


class TelemetrySource(str, Enum):
    """Where the payload the frontend is looking at actually came from.

    This is the field the whole graceful-degradation story hangs on: the UI's
    mode toggle reflects *this*, never a frontend-local flag.
    """

    LIVE = "live"
    MOCK = "mock"


# -----------------------------------------------------------------------------
# Core entities
# -----------------------------------------------------------------------------
class QPUBackend(BaseModel):
    """A single quantum processing unit."""

    name: str = Field(description="IBM backend identifier, e.g. 'ibm_torino'.")
    status: BackendStatus = BackendStatus.UNKNOWN
    status_reason: str | None = Field(
        default=None, description="IBM's explanation when a QPU is paused or offline."
    )
    queue_length: int = Field(default=0, ge=0, description="Jobs waiting to execute.")
    qubits: int = Field(default=0, ge=0, description="Programmable qubit count.")
    clops: int | None = Field(
        default=None, description="Circuit Layer Operations Per Second; higher is faster."
    )
    is_simulator: bool = False
    processor_type: str | None = Field(
        default=None, description="Processor family, e.g. 'Heron r2'."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def estimated_wait_seconds(self) -> int:
        """Rough time-to-start, derived rather than reported.

        IBM's documented /v1/backends response does **not** include a wait-time
        field, so we estimate it instead of inventing one: queue depth times a
        nominal per-job service time, scaled down for faster processors. This is
        surfaced to the UI clearly labelled as an estimate.
        """
        if not self.status.is_accepting_jobs:
            return 0
        # Nominal seconds a single Runtime job occupies a QPU, empirically ~45s.
        nominal_service_seconds = 45.0
        if self.clops:
            # Normalise against a 180k CLOPS reference machine; clamp so an
            # unusually fast or slow device cannot produce absurd estimates.
            speed_factor = max(0.4, min(2.5, 180_000 / max(self.clops, 1)))
        else:
            speed_factor = 1.0
        return int(self.queue_length * nominal_service_seconds * speed_factor)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_operational(self) -> bool:
        return self.status.is_accepting_jobs


class QuantumJob(BaseModel):
    """A Runtime job observed on the account."""

    id: str
    backend: str
    status: JobStatus = JobStatus.UNKNOWN
    program_id: str | None = None
    created: datetime
    session_id: str | None = None
    qpu_charge_time_seconds: float | None = None
    tags: list[str] = Field(default_factory=list)


class BackendRecommendation(BaseModel):
    """One entry in the 'where should I submit right now?' ranking."""

    backend_name: str
    score: float = Field(ge=0.0, le=100.0)
    rank: int = Field(ge=1)
    rationale: str
    queue_length: int
    qubits: int
    estimated_wait_seconds: int


class FleetSummary(BaseModel):
    """Headline numbers across the whole fleet."""

    total_backends: int = 0
    online_backends: int = 0
    paused_backends: int = 0
    offline_backends: int = 0
    total_qubits_available: int = 0
    total_queued_jobs: int = 0
    jobs_in_flight: int = 0
    median_queue_depth: float = 0.0
    busiest_backend: str | None = None
    quietest_operational_backend: str | None = None


class TelemetrySnapshot(BaseModel):
    """The single payload shape pushed over the WebSocket and returned by REST.

    Live and mock telemetry are the *same* type; only `source` differs.
    """

    source: TelemetrySource
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this snapshot was produced by the backend.",
    )
    backends: list[QPUBackend] = Field(default_factory=list)
    jobs: list[QuantumJob] = Field(default_factory=list)
    summary: FleetSummary = Field(default_factory=FleetSummary)
    recommendations: list[BackendRecommendation] = Field(default_factory=list)

    # Diagnostics -- shown in the UI footer so the operator knows why the
    # dashboard is in whichever mode it is in.
    degraded_reason: str | None = Field(
        default=None,
        description="Populated when source == 'mock' despite live mode being configured.",
    )
    poll_interval_seconds: float = 12.0
    consecutive_failures: int = 0
    will_retry: bool = Field(
        default=True,
        description=(
            "False when the poller has stopped attempting live calls (a rejected "
            "credential). The UI must not promise an automatic recovery that is "
            "not coming."
        ),
    )


class HistoryPoint(BaseModel):
    """One persisted queue-depth observation for a single backend."""

    backend: str
    queue_length: int
    recorded_at: datetime


class HealthResponse(BaseModel):
    status: str
    source: TelemetrySource
    live_mode_configured: bool
    poller_running: bool
    last_successful_poll: datetime | None = None
    consecutive_failures: int = 0
    connected_websocket_clients: int = 0
    history_enabled: bool = False
