"""The simulator has to look real, not merely be random."""

from backend.mock_service import MockTelemetryService
from backend.models import BackendStatus, JobStatus


def test_bootstraps_a_full_fleet():
    backends, jobs = MockTelemetryService(seed=1).snapshot()
    assert len(backends) == 18
    assert all(b.qubits > 0 for b in backends)
    # The job stream is pre-populated so the very first render is never empty.
    assert len(jobs) >= 20


def test_is_deterministic_under_a_seed():
    a, _ = MockTelemetryService(seed=42).tick()
    b, _ = MockTelemetryService(seed=42).tick()
    assert [x.queue_length for x in a] == [x.queue_length for x in b]


def test_queues_drift_rather_than_jump():
    """A dashboard fed by independent random draws jitters and looks fake.

    Successive ticks must be *correlated*: a queue of 120 may become 118 or 125,
    never 4. This is the property that makes the line chart believable.
    """
    service = MockTelemetryService(seed=7)
    previous = {b.name: b.queue_length for b in service.tick()[0]}

    for _ in range(40):
        for backend in service.tick()[0]:
            if backend.is_simulator:
                continue
            delta = abs(backend.queue_length - previous[backend.name])
            assert delta <= 40, f"{backend.name} jumped by {delta} in one tick"
            previous[backend.name] = backend.queue_length


def test_queues_stay_in_a_believable_band():
    """Mean-reversion must stop the random walk wandering off to 0 or 10,000."""
    service = MockTelemetryService(seed=3)
    for _ in range(300):
        backends, _ = service.tick()
    real_hardware = [b for b in backends if not b.is_simulator]
    assert all(0 <= b.queue_length <= 600 for b in real_hardware)
    # Across 17 machines and 300 ticks, at least some queue should be non-trivial.
    assert any(b.queue_length > 10 for b in real_hardware)


def test_calibration_outages_occur_and_resolve():
    """Devices should go down for maintenance and come back, as real ones do."""
    service = MockTelemetryService(seed=11)
    saw_outage = False
    for _ in range(400):
        backends, _ = service.tick()
        down = [b for b in backends if b.status is not BackendStatus.ONLINE]
        if down:
            saw_outage = True
            # An outage must always carry a human-readable explanation.
            assert all(b.status_reason for b in down)
    assert saw_outage, "no calibration outage occurred in 400 ticks"

    # And the fleet must recover rather than decaying permanently offline.
    for _ in range(200):
        backends, _ = service.tick()
    online = sum(1 for b in backends if b.status is BackendStatus.ONLINE)
    assert online >= 12


def test_jobs_advance_through_their_lifecycle():
    service = MockTelemetryService(seed=5)
    initial_terminal = sum(1 for j in service.snapshot()[1] if j.status.is_terminal)
    for _ in range(30):
        _, jobs = service.tick()
    later_terminal = sum(1 for j in jobs if j.status.is_terminal)
    assert later_terminal >= initial_terminal or len(jobs) == 60


def test_job_stream_stays_bounded():
    service = MockTelemetryService(seed=9)
    for _ in range(200):
        _, jobs = service.tick()
    assert len(jobs) <= 60
    # Newest first -- the frontend relies on this ordering.
    assert jobs == sorted(jobs, key=lambda j: j.created, reverse=True)


def test_finished_jobs_report_charge_time():
    service = MockTelemetryService(seed=13)
    for _ in range(50):
        _, jobs = service.tick()
    done = [j for j in jobs if j.status is JobStatus.COMPLETED]
    assert done and all(j.qpu_charge_time_seconds is not None for j in done)


def test_can_resume_from_persisted_queue_depths():
    """A restart must continue the story, not restart it.

    Without this the chart shows a cliff where stored history meets a freshly
    randomised fleet.
    """
    service = MockTelemetryService(seed=17)
    service.seed_queue_depths({"ibm_fez": 137, "ibm_torino": 4})
    backends = {b.name: b.queue_length for b in service.snapshot()[0]}
    assert backends["ibm_fez"] == 137
    assert backends["ibm_torino"] == 4


def test_seeding_ignores_unknown_backends_and_clamps_absurd_values():
    service = MockTelemetryService(seed=19)
    service.seed_queue_depths({"not_a_real_qpu": 50, "ibm_fez": 99_999, "ibm_kyiv": -8})
    backends = {b.name: b.queue_length for b in service.snapshot()[0]}
    assert "not_a_real_qpu" not in backends
    assert backends["ibm_fez"] == 600
    assert backends["ibm_kyiv"] == 0
