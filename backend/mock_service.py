"""Physically plausible simulated telemetry.

This is deliberately written *before* the live IBM client, for two reasons:

1. The entire dashboard can be built, demoed and graded with zero credentials.
2. It is the fallback the poller swaps in when IBM is unreachable, so it has to
   emit exactly the same types as the live path (see `models.py`).

The simulator is **stateful**: queue depths random-walk from one tick to the
next rather than being re-rolled independently, QPUs occasionally drop into
calibration, and jobs advance through their lifecycle. A dashboard fed by
independent random draws looks obviously fake -- the line chart jitters
violently instead of drifting. This one drifts.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from .models import (
    BackendStatus,
    JobStatus,
    QPUBackend,
    QuantumJob,
)

# -----------------------------------------------------------------------------
# Fleet definition -- real IBM device names, families and qubit counts, so the
# simulated dashboard is recognisable to anyone who has used IBM Quantum.
# -----------------------------------------------------------------------------
_FLEET: tuple[dict, ...] = (
    {"name": "ibm_torino", "qubits": 133, "processor_type": "Heron r1", "clops": 210_000},
    {"name": "ibm_fez", "qubits": 156, "processor_type": "Heron r2", "clops": 195_000},
    {"name": "ibm_marrakesh", "qubits": 156, "processor_type": "Heron r2", "clops": 195_000},
    {"name": "ibm_kingston", "qubits": 156, "processor_type": "Heron r2", "clops": 192_000},
    {"name": "ibm_aachen", "qubits": 156, "processor_type": "Heron r2", "clops": 188_000},
    {"name": "ibm_brisbane", "qubits": 127, "processor_type": "Eagle r3", "clops": 32_000},
    {"name": "ibm_sherbrooke", "qubits": 127, "processor_type": "Eagle r3", "clops": 30_000},
    {"name": "ibm_kyiv", "qubits": 127, "processor_type": "Eagle r3", "clops": 30_000},
    {"name": "ibm_quebec", "qubits": 127, "processor_type": "Eagle r3", "clops": 29_000},
    {"name": "ibm_brussels", "qubits": 127, "processor_type": "Eagle r3", "clops": 29_500},
    {"name": "ibm_strasbourg", "qubits": 127, "processor_type": "Eagle r3", "clops": 28_800},
    {"name": "ibm_rensselaer", "qubits": 127, "processor_type": "Eagle r3", "clops": 28_000},
    {"name": "ibm_kyoto", "qubits": 127, "processor_type": "Eagle r3", "clops": 27_500},
    {"name": "ibm_osaka", "qubits": 127, "processor_type": "Eagle r3", "clops": 27_000},
    {"name": "ibm_nazca", "qubits": 127, "processor_type": "Eagle r3", "clops": 26_500},
    {"name": "ibm_cusco", "qubits": 127, "processor_type": "Eagle r3", "clops": 26_000},
    {"name": "ibm_pittsburgh", "qubits": 156, "processor_type": "Heron r2", "clops": 190_000},
    {"name": "simulator_statevector", "qubits": 32, "processor_type": "Simulator",
     "clops": None, "is_simulator": True},
)

_PROGRAM_IDS = ("sampler", "estimator", "circuit-runner", "qasm3-runner")

_CALIBRATION_REASONS = (
    "Scheduled calibration in progress",
    "Device under maintenance",
    "Recalibrating readout",
    "Internal error; engineers notified",
)


class MockTelemetryService:
    """Generates a coherent, evolving view of a fictional QPU fleet.

    A single long-lived instance is held by the poller, so successive calls to
    :meth:`tick` produce a continuous story rather than unrelated snapshots.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._backends: dict[str, dict] = {}
        self._jobs: list[QuantumJob] = []
        # Countdown of remaining ticks a backend stays out of service.
        self._outage_ticks: dict[str, int] = {}
        self._bootstrap()

    # -- setup ---------------------------------------------------------------
    def _bootstrap(self) -> None:
        """Seed each backend with a plausible starting queue."""
        for spec in _FLEET:
            is_sim = spec.get("is_simulator", False)
            # Simulators are effectively unqueued; real hardware is busy.
            base_queue = 0 if is_sim else self._rng.randint(2, 180)
            self._backends[spec["name"]] = {
                **spec,
                "is_simulator": is_sim,
                "queue_length": base_queue,
                "status": BackendStatus.ONLINE,
                "status_reason": None,
            }
        # Start with a populated job stream so the first render is never empty.
        for _ in range(28):
            self._jobs.append(self._spawn_job(age_seconds=self._rng.randint(0, 2400)))
        self._jobs.sort(key=lambda job: job.created, reverse=True)

    def _spawn_job(self, age_seconds: int = 0) -> QuantumJob:
        operational = [
            name for name, b in self._backends.items()
            if b["status"] is BackendStatus.ONLINE
        ] or list(self._backends)
        backend = self._rng.choice(operational)
        created = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

        # Older jobs are more likely to have finished -- that is what makes the
        # stream read like a real timeline rather than a shuffled list.
        if age_seconds > 900:
            status = self._rng.choices(
                [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED],
                weights=[88, 8, 4],
            )[0]
        elif age_seconds > 240:
            status = self._rng.choices(
                [JobStatus.COMPLETED, JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.FAILED],
                weights=[52, 22, 20, 6],
            )[0]
        else:
            status = self._rng.choices(
                [JobStatus.QUEUED, JobStatus.RUNNING], weights=[72, 28]
            )[0]

        return QuantumJob(
            id=f"c{uuid.uuid4().hex[:23]}",
            backend=backend,
            status=status,
            program_id=self._rng.choice(_PROGRAM_IDS),
            created=created,
            session_id=(uuid.uuid4().hex[:16] if self._rng.random() < 0.35 else None),
            qpu_charge_time_seconds=(
                round(self._rng.uniform(1.2, 95.0), 2) if status.is_terminal else None
            ),
            tags=self._rng.sample(
                ["vqe", "qaoa", "benchmark", "tutorial", "chemistry", "optimisation"],
                k=self._rng.randint(0, 2),
            ),
        )

    def seed_queue_depths(self, depths: dict[str, int]) -> None:
        """Resume the simulation from previously observed queue depths.

        Called on startup with the last values recorded in history, so a restart
        continues the story instead of discontinuously restarting it.
        """
        for name, depth in depths.items():
            if name in self._backends:
                self._backends[name]["queue_length"] = max(0, min(600, int(depth)))

    # -- per-tick evolution --------------------------------------------------
    def _advance_backends(self) -> None:
        for name, backend in self._backends.items():
            # 1. Resolve or continue an existing outage.
            if self._outage_ticks.get(name, 0) > 0:
                self._outage_ticks[name] -= 1
                if self._outage_ticks[name] == 0:
                    backend["status"] = BackendStatus.ONLINE
                    backend["status_reason"] = None
                continue

            # 2. Occasionally take a real device down for calibration.
            #    ~0.6% per backend per tick keeps roughly 1-2 devices out at any
            #    moment across an 18-machine fleet -- which matches reality.
            if not backend["is_simulator"] and self._rng.random() < 0.006:
                backend["status"] = self._rng.choice(
                    [BackendStatus.PAUSED, BackendStatus.OFFLINE]
                )
                backend["status_reason"] = self._rng.choice(_CALIBRATION_REASONS)
                self._outage_ticks[name] = self._rng.randint(8, 40)
                continue

            # 3. Random-walk the queue. Drift is mean-reverting: a very long
            #    queue is more likely to shrink, a short one to grow, so depths
            #    stay in a believable band instead of wandering to zero or 5000.
            if backend["is_simulator"]:
                backend["queue_length"] = max(0, self._rng.randint(0, 3))
                continue

            current = backend["queue_length"]
            pull_to_centre = (90 - current) / 90.0  # +ve below 90, -ve above
            drift = self._rng.gauss(mu=pull_to_centre * 2.0, sigma=5.0)
            backend["queue_length"] = max(0, min(600, int(current + drift)))

    def _advance_jobs(self) -> None:
        now = datetime.now(timezone.utc)
        for job in self._jobs:
            if job.status.is_terminal:
                continue
            if job.status is JobStatus.QUEUED and self._rng.random() < 0.22:
                job.status = JobStatus.RUNNING
            elif job.status is JobStatus.RUNNING and self._rng.random() < 0.30:
                job.status = self._rng.choices(
                    [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED],
                    weights=[90, 7, 3],
                )[0]
                job.qpu_charge_time_seconds = round(self._rng.uniform(1.2, 95.0), 2)

        # Submit new work, then keep the stream bounded.
        for _ in range(self._rng.randint(0, 3)):
            self._jobs.insert(0, self._spawn_job())
        self._jobs = sorted(self._jobs, key=lambda j: j.created, reverse=True)[:60]
        # Silence an unused-variable warning while keeping `now` documented as
        # the conceptual clock for this tick.
        del now

    # -- public API ----------------------------------------------------------
    def tick(self) -> tuple[list[QPUBackend], list[QuantumJob]]:
        """Advance the simulation one step and return the current view."""
        self._advance_backends()
        self._advance_jobs()
        return self.snapshot()

    def snapshot(self) -> tuple[list[QPUBackend], list[QuantumJob]]:
        """Return the current view without advancing the simulation."""
        backends = [
            QPUBackend(
                name=b["name"],
                status=b["status"],
                status_reason=b["status_reason"],
                queue_length=b["queue_length"],
                qubits=b["qubits"],
                clops=b["clops"],
                is_simulator=b["is_simulator"],
                processor_type=b["processor_type"],
            )
            for b in self._backends.values()
        ]
        return backends, list(self._jobs)
