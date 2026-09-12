"""The normalisation layer is where IBM's vocabulary meets ours."""

from datetime import datetime, timezone

from backend.models import BackendStatus, JobStatus, QPUBackend, QuantumJob


class TestBackendStatus:
    def test_parses_ibm_vocabulary(self):
        assert BackendStatus.parse("online") is BackendStatus.ONLINE
        assert BackendStatus.parse("paused") is BackendStatus.PAUSED
        assert BackendStatus.parse("offline") is BackendStatus.OFFLINE

    def test_is_case_and_whitespace_insensitive(self):
        assert BackendStatus.parse("  ONLINE  ") is BackendStatus.ONLINE

    def test_unknown_values_degrade_rather_than_raise(self):
        # A new IBM status must not crash the poller.
        assert BackendStatus.parse("hibernating") is BackendStatus.UNKNOWN
        assert BackendStatus.parse(None) is BackendStatus.UNKNOWN

    def test_only_online_accepts_jobs(self):
        assert BackendStatus.ONLINE.is_accepting_jobs
        assert not BackendStatus.PAUSED.is_accepting_jobs
        assert not BackendStatus.OFFLINE.is_accepting_jobs


class TestJobStatus:
    def test_parses_ibms_title_case(self):
        assert JobStatus.parse("Queued") is JobStatus.QUEUED
        assert JobStatus.parse("Running") is JobStatus.RUNNING
        assert JobStatus.parse("Completed") is JobStatus.COMPLETED
        assert JobStatus.parse("Cancelled") is JobStatus.CANCELLED
        assert JobStatus.parse("Failed") is JobStatus.FAILED

    def test_folds_legacy_synonyms(self):
        # Older IBM responses and the original spec used these spellings.
        assert JobStatus.parse("DONE") is JobStatus.COMPLETED
        assert JobStatus.parse("ERROR") is JobStatus.FAILED
        assert JobStatus.parse("Canceled") is JobStatus.CANCELLED  # one 'l'
        assert JobStatus.parse("VALIDATING") is JobStatus.QUEUED

    def test_terminal_classification(self):
        assert JobStatus.COMPLETED.is_terminal
        assert JobStatus.FAILED.is_terminal
        assert JobStatus.CANCELLED.is_terminal
        assert not JobStatus.QUEUED.is_terminal
        assert not JobStatus.RUNNING.is_terminal


class TestEstimatedWait:
    """IBM does not publish a wait time, so we derive one. It must be sane."""

    def test_offline_backend_reports_no_wait(self):
        backend = QPUBackend(
            name="ibm_x", status=BackendStatus.OFFLINE, queue_length=500, qubits=127
        )
        assert backend.estimated_wait_seconds == 0

    def test_wait_grows_with_queue(self):
        short = QPUBackend(name="a", status=BackendStatus.ONLINE, queue_length=10, qubits=127)
        long = QPUBackend(name="b", status=BackendStatus.ONLINE, queue_length=200, qubits=127)
        assert long.estimated_wait_seconds > short.estimated_wait_seconds

    def test_faster_processor_predicts_shorter_wait(self):
        slow = QPUBackend(
            name="eagle", status=BackendStatus.ONLINE, queue_length=100, qubits=127, clops=30_000
        )
        fast = QPUBackend(
            name="heron", status=BackendStatus.ONLINE, queue_length=100, qubits=156, clops=195_000
        )
        assert fast.estimated_wait_seconds < slow.estimated_wait_seconds

    def test_speed_factor_is_clamped(self):
        """An absurd CLOPS value must not produce an absurd estimate."""
        absurd = QPUBackend(
            name="x", status=BackendStatus.ONLINE, queue_length=10, qubits=127, clops=1
        )
        # Clamped at 2.5x the nominal 45s service time.
        assert absurd.estimated_wait_seconds <= 10 * 45 * 2.5


def test_job_accepts_timezone_aware_creation():
    job = QuantumJob(
        id="abc", backend="ibm_fez", status=JobStatus.RUNNING,
        created=datetime.now(timezone.utc),
    )
    assert job.created.tzinfo is not None
