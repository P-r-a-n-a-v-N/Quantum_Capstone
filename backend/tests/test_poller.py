"""The graceful-degradation guarantee, asserted rather than hoped for.

The claim this project makes is: *the dashboard never shows an error and never
shows a blank page.* That claim is only worth making if it is tested. Every
failure mode IBM can present is exercised here.
"""

import pytest

from backend.config import Settings
from backend.core.cache import TelemetryStore
from backend.core.ibm_client import IBMAuthError, IBMQuantumError
from backend.core.poller import TelemetryPoller
from backend.models import BackendStatus, QPUBackend, TelemetrySource


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ibm_quantum_api_key="k",
        ibm_quantum_crn="crn",
        poll_interval_seconds=10.0,
        enable_history=False,
    )


@pytest.fixture
def store() -> TelemetryStore:
    return TelemetryStore(ttl_seconds=60)


class _FakeClient:
    """Stands in for IBMQuantumClient with scriptable behaviour."""

    def __init__(self, backends=None, jobs=None, raises: Exception | None = None):
        self._backends = backends or []
        self._jobs = jobs or []
        self._raises = raises
        self.backend_calls = 0

    async def fetch_backends(self):
        self.backend_calls += 1
        if self._raises:
            raise self._raises
        return self._backends

    async def fetch_jobs(self, limit: int = 50):
        if self._raises:
            raise self._raises
        return self._jobs

    async def aclose(self):
        pass


def _live_fleet():
    return [
        QPUBackend(name="ibm_fez", status=BackendStatus.ONLINE, queue_length=12,
                   qubits=156, clops=195_000),
        QPUBackend(name="ibm_brisbane", status=BackendStatus.ONLINE, queue_length=300,
                   qubits=127, clops=32_000),
    ]


class TestLivePath:
    async def test_serves_live_telemetry_when_ibm_responds(self, settings, store):
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(backends=_live_fleet()))
        snapshot = await poller.poll_once()

        assert snapshot.source is TelemetrySource.LIVE
        assert snapshot.degraded_reason is None
        assert len(snapshot.backends) == 2
        assert store.consecutive_failures == 0
        assert store.last_successful_poll is not None

    async def test_snapshot_is_written_to_the_cache(self, settings, store):
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(backends=_live_fleet()))
        await poller.poll_once()
        assert store.is_populated
        assert store.get_snapshot().source is TelemetrySource.LIVE

    async def test_backends_are_ordered_operational_first_then_shortest_queue(
        self, settings, store
    ):
        fleet = [
            QPUBackend(name="busy", status=BackendStatus.ONLINE, queue_length=400, qubits=127),
            QPUBackend(name="down", status=BackendStatus.OFFLINE, queue_length=0, qubits=127),
            QPUBackend(name="quiet", status=BackendStatus.ONLINE, queue_length=3, qubits=127),
        ]
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(backends=fleet))
        snapshot = await poller.poll_once()
        assert [b.name for b in snapshot.backends] == ["quiet", "busy", "down"]


class TestGracefulDegradation:
    @pytest.mark.parametrize(
        "failure",
        [
            IBMQuantumError("IBM unreachable"),
            IBMQuantumError("Rate limited by IBM"),
            TimeoutError("read timeout"),
            RuntimeError("something nobody predicted"),
        ],
        ids=["unreachable", "rate-limited", "timeout", "unknown"],
    )
    async def test_any_failure_falls_back_to_mock_without_raising(
        self, settings, store, failure
    ):
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(raises=failure))
        snapshot = await poller.poll_once()  # must not raise

        assert snapshot.source is TelemetrySource.MOCK
        assert snapshot.degraded_reason, "the UI needs a reason to display"
        assert snapshot.will_retry is True, "a transient failure will be retried"
        # Crucially: the dashboard still has content to render.
        assert len(snapshot.backends) == 18
        assert store.consecutive_failures == 1

    async def test_an_empty_backend_list_counts_as_a_failure(self, settings, store):
        """A 200 OK with no devices is not success -- it would blank the grid."""
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(backends=[]))
        snapshot = await poller.poll_once()
        assert snapshot.source is TelemetrySource.MOCK
        assert snapshot.backends

    async def test_consecutive_failures_accumulate_then_reset_on_recovery(
        self, settings, store
    ):
        failing = _FakeClient(raises=IBMQuantumError("down"))
        poller = TelemetryPoller(settings, store, ibm_client=failing)
        for _ in range(3):
            await poller.poll_once()
        assert store.consecutive_failures == 3

        # IBM comes back.
        poller._client = _FakeClient(backends=_live_fleet())
        snapshot = await poller.poll_once()
        assert snapshot.source is TelemetrySource.LIVE
        assert store.consecutive_failures == 0

    async def test_backoff_grows_with_failures_but_is_capped(self, settings, store):
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(raises=IBMQuantumError("x")))
        assert poller._next_delay() == 10.0
        store.consecutive_failures = 2
        assert poller._next_delay() == 30.0
        store.consecutive_failures = 99
        assert poller._next_delay() == 50.0, "backoff must be capped so recovery stays quick"

    async def test_no_backoff_once_we_have_stopped_calling_ibm(self, settings, store):
        """Backing off spares IBM. If we are not calling IBM, it only slows the UI."""
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(raises=IBMAuthError("bad")))
        await poller.poll_once()  # latches
        assert store.consecutive_failures == 1
        assert poller._next_delay() == 10.0, "must return to the base interval once latched"

    async def test_mock_only_deployment_never_backs_off(self, store):
        settings = Settings(ibm_quantum_api_key="", ibm_quantum_crn="", enable_history=False)
        poller = TelemetryPoller(settings, store)
        store.consecutive_failures = 4
        assert poller._next_delay() == settings.poll_interval_seconds


class TestAuthFailureLatching:
    async def test_a_rejected_key_stops_further_attempts(self, settings, store):
        """Retrying a wrong API key every 12s just spams IAM. Latch it instead."""
        client = _FakeClient(raises=IBMAuthError("bad key"))
        poller = TelemetryPoller(settings, store, ibm_client=client)

        first = await poller.poll_once()
        assert first.source is TelemetrySource.MOCK
        assert "authentication failed" in first.degraded_reason.lower()
        assert client.backend_calls == 1

        for _ in range(5):
            await poller.poll_once()
        assert client.backend_calls == 1, "must not keep retrying a rejected credential"
        # The UI must not promise a recovery that is never coming.
        assert first.will_retry is False

    async def test_the_rejection_reason_survives_every_later_snapshot(self, settings, store):
        """The reason must persist, not just appear on the first tick.

        Without this the UI shows the amber warning once and then silently
        reverts to "no credentials configured" -- telling the user their key is
        absent when in fact it was rejected.
        """
        poller = TelemetryPoller(settings, store, ibm_client=_FakeClient(raises=IBMAuthError("bad key")))
        await poller.poll_once()

        for _ in range(5):
            snapshot = await poller.poll_once()
            assert snapshot.source is TelemetrySource.MOCK
            assert snapshot.degraded_reason, "the rejection reason must keep being reported"
            assert "authentication failed" in snapshot.degraded_reason.lower()


class TestMockByConfiguration:
    async def test_no_credentials_is_mock_mode_without_a_degraded_reason(self, store):
        """Running without a key is the documented default, not a failure."""
        settings = Settings(ibm_quantum_api_key="", ibm_quantum_crn="", enable_history=False)
        poller = TelemetryPoller(settings, store)
        snapshot = await poller.poll_once()

        assert snapshot.source is TelemetrySource.MOCK
        assert snapshot.degraded_reason is None
        assert len(snapshot.backends) == 18

    async def test_force_mock_mode_overrides_valid_credentials(self, store):
        settings = Settings(
            ibm_quantum_api_key="k", ibm_quantum_crn="crn",
            force_mock_mode=True, enable_history=False,
        )
        poller = TelemetryPoller(settings, store)
        assert poller._client is None
        assert (await poller.poll_once()).source is TelemetrySource.MOCK


class TestBroadcast:
    async def test_snapshot_is_pushed_to_subscribers(self, settings, store):
        received = []

        async def capture(snapshot):
            received.append(snapshot)

        poller = TelemetryPoller(
            settings, store, ibm_client=_FakeClient(backends=_live_fleet()), broadcast=capture
        )
        await poller.poll_once()
        assert len(received) == 1 and received[0].source is TelemetrySource.LIVE

    async def test_a_broken_broadcast_does_not_break_the_poll(self, settings, store):
        async def exploding(_snapshot):
            raise RuntimeError("websocket layer is on fire")

        poller = TelemetryPoller(
            settings, store, ibm_client=_FakeClient(backends=_live_fleet()), broadcast=exploding
        )
        await poller.poll_once()  # must not raise
        assert store.is_populated, "the cache must still hold the snapshot"
