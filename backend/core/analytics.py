"""Fleet analytics: headline summary numbers and the QPU recommender.

The recommender is what turns this from a passive display into a decision tool.
It answers the question an actual user has -- *"which machine should I submit to
right now?"* -- by scoring every operational backend on the three things that
determine how long you will wait for a useful result:

    queue depth   55%   how many jobs are ahead of you
    throughput    25%   CLOPS, i.e. how fast the device clears work
    capability    20%   qubit count, as a tie-breaker

Queue depth is deliberately weighted above the other two *combined*. An earlier
45/30/25 split let a 156-qubit machine with 500 jobs queued outrank an idle
127-qubit one -- which is precisely the recommendation a user does not want.
Capability is only a tie-breaker here because it is really a *constraint*, and
constraints belong in the filter: callers pass `min_qubits` to exclude devices
their circuit cannot fit on, rather than hoping a soft score handles it.

Scores are normalised against the current fleet rather than absolute constants,
so the ranking stays meaningful whether every machine is idle or every machine
is swamped.
"""

from __future__ import annotations

import statistics

from ..models import (
    BackendRecommendation,
    BackendStatus,
    FleetSummary,
    JobStatus,
    QPUBackend,
    QuantumJob,
)

_WEIGHT_QUEUE = 0.55
_WEIGHT_SPEED = 0.25
_WEIGHT_QUBITS = 0.20


def build_summary(backends: list[QPUBackend], jobs: list[QuantumJob]) -> FleetSummary:
    """Compute the headline KPI bar shown across the top of the dashboard."""
    if not backends:
        return FleetSummary()

    operational = [b for b in backends if b.status is BackendStatus.ONLINE]
    real_hardware = [b for b in backends if not b.is_simulator]
    queue_depths = [b.queue_length for b in operational] or [0]

    busiest = max(backends, key=lambda b: b.queue_length, default=None)
    quietest = min(operational, key=lambda b: b.queue_length, default=None)

    return FleetSummary(
        total_backends=len(backends),
        online_backends=len(operational),
        paused_backends=sum(1 for b in backends if b.status is BackendStatus.PAUSED),
        offline_backends=sum(1 for b in backends if b.status is BackendStatus.OFFLINE),
        # Only count qubits you can actually submit to, and only on real hardware.
        total_qubits_available=sum(
            b.qubits for b in real_hardware if b.status is BackendStatus.ONLINE
        ),
        total_queued_jobs=sum(b.queue_length for b in backends),
        jobs_in_flight=sum(
            1 for job in jobs if job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
        ),
        median_queue_depth=round(statistics.median(queue_depths), 1),
        busiest_backend=busiest.name if busiest and busiest.queue_length > 0 else None,
        quietest_operational_backend=quietest.name if quietest else None,
    )


def _normalise(value: float, lowest: float, highest: float, *, higher_is_better: bool) -> float:
    """Scale `value` into 0..1 against the observed fleet range.

    When every backend shares the same value the range collapses, so we return a
    neutral 0.5 rather than dividing by zero.
    """
    if highest <= lowest:
        return 0.5
    scaled = (value - lowest) / (highest - lowest)
    return scaled if higher_is_better else 1.0 - scaled


def recommend_backends(
    backends: list[QPUBackend],
    *,
    limit: int = 3,
    min_qubits: int = 0,
    include_simulators: bool = False,
) -> list[BackendRecommendation]:
    """Rank operational backends by how good a submission target they are now."""
    candidates = [
        b
        for b in backends
        if b.status is BackendStatus.ONLINE
        and b.qubits >= min_qubits
        and (include_simulators or not b.is_simulator)
    ]
    if not candidates:
        return []

    queues = [b.queue_length for b in candidates]
    qubit_counts = [b.qubits for b in candidates]
    speeds = [b.clops or 0 for b in candidates]

    q_lo, q_hi = min(queues), max(queues)
    n_lo, n_hi = min(qubit_counts), max(qubit_counts)
    s_lo, s_hi = min(speeds), max(speeds)

    scored: list[tuple[float, QPUBackend, str]] = []
    for backend in candidates:
        queue_score = _normalise(backend.queue_length, q_lo, q_hi, higher_is_better=False)
        qubit_score = _normalise(backend.qubits, n_lo, n_hi, higher_is_better=True)
        speed_score = _normalise(backend.clops or 0, s_lo, s_hi, higher_is_better=True)

        total = (
            queue_score * _WEIGHT_QUEUE
            + qubit_score * _WEIGHT_QUBITS
            + speed_score * _WEIGHT_SPEED
        ) * 100.0

        scored.append((total, backend, _explain(backend, queue_score, qubit_score, speed_score)))

    scored.sort(key=lambda item: item[0], reverse=True)

    return [
        BackendRecommendation(
            backend_name=backend.name,
            score=round(score, 1),
            rank=index,
            rationale=rationale,
            queue_length=backend.queue_length,
            qubits=backend.qubits,
            estimated_wait_seconds=backend.estimated_wait_seconds,
        )
        for index, (score, backend, rationale) in enumerate(scored[:limit], start=1)
    ]


def _explain(
    backend: QPUBackend, queue_score: float, qubit_score: float, speed_score: float
) -> str:
    """Say *why* a backend ranked where it did, in one human sentence.

    A score with no explanation is not a decision tool, it is a number.
    """
    drivers: list[str] = []
    if queue_score >= 0.66:
        drivers.append(f"short queue ({backend.queue_length} jobs)")
    elif queue_score <= 0.33:
        drivers.append(f"long queue ({backend.queue_length} jobs)")

    if qubit_score >= 0.66:
        drivers.append(f"high capacity ({backend.qubits} qubits)")
    elif qubit_score <= 0.33:
        drivers.append(f"limited capacity ({backend.qubits} qubits)")

    if speed_score >= 0.66 and backend.clops:
        drivers.append(f"fast processor ({backend.clops:,} CLOPS)")
    elif speed_score <= 0.33 and backend.clops:
        drivers.append("slower processor")

    if not drivers:
        return "Balanced across queue depth, capacity and throughput."

    wait_minutes = backend.estimated_wait_seconds // 60
    wait_phrase = f"~{wait_minutes} min estimated wait" if wait_minutes else "no meaningful wait"
    # Sentence-case only the first character: str.capitalize() would lower-case
    # the rest and turn "195,000 CLOPS" into "195,000 clops".
    joined = ", ".join(drivers)
    sentence = joined[0].upper() + joined[1:]
    return f"{sentence}; {wait_phrase}."
