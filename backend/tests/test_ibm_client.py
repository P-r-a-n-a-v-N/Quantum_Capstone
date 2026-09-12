"""Contract tests for the live IBM client, using mocked HTTP.

These pin down the details that are easy to get wrong and impossible to notice
until the real API rejects you: the third required header, the nested status
object, and the token refresh margin.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from backend.config import Settings
from backend.core.ibm_client import IBMAuthError, IBMQuantumClient, IBMQuantumError
from backend.models import BackendStatus, JobStatus

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
API_URL = "https://quantum.cloud.ibm.com/api/v1"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ibm_quantum_api_key="test-key",
        ibm_quantum_crn="crn:v1:bluemix:public:quantum-computing:us-east:a/test::",
        ibm_quantum_api_url=API_URL,
        ibm_iam_url=IAM_URL,
        ibm_api_version="2024-01-01",
        enable_history=False,
    )


def _token_response(expires_in: int = 3600) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "tok-123", "expires_in": expires_in, "token_type": "Bearer"}
    )


class TestAuthentication:
    @respx.mock
    async def test_exchanges_api_key_for_bearer_token(self, settings):
        route = respx.post(IAM_URL).mock(return_value=_token_response())
        async with IBMQuantumClient(settings) as client:
            assert await client.get_token() == "tok-123"

        sent = route.calls[0].request
        body = sent.content.decode()
        assert "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Aapikey" in body
        assert "apikey=test-key" in body

    @respx.mock
    async def test_token_is_cached_across_calls(self, settings):
        route = respx.post(IAM_URL).mock(return_value=_token_response())
        async with IBMQuantumClient(settings) as client:
            for _ in range(5):
                await client.get_token()
        assert route.call_count == 1, "token should be fetched once, not per request"

    @respx.mock
    async def test_refreshes_proactively_before_expiry(self, settings):
        """A token that expires inside the safety margin must be replaced early.

        Waiting for a 401 and retrying would leak a failed poll into the UI.
        """
        settings.token_refresh_margin_seconds = 300
        route = respx.post(IAM_URL).mock(return_value=_token_response(expires_in=3600))
        async with IBMQuantumClient(settings) as client:
            await client.get_token()
            # Pretend we are now inside the 5-minute margin.
            client._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
            await client.get_token()
        assert route.call_count == 2

    @respx.mock
    async def test_rejected_api_key_raises_auth_error(self, settings):
        respx.post(IAM_URL).mock(return_value=httpx.Response(400, json={"errorCode": "BXNIM0415E"}))
        async with IBMQuantumClient(settings) as client:
            with pytest.raises(IBMAuthError):
                await client.get_token()

    @respx.mock
    async def test_network_failure_raises_quantum_error(self, settings):
        respx.post(IAM_URL).mock(side_effect=httpx.ConnectError("dns failure"))
        async with IBMQuantumClient(settings) as client:
            with pytest.raises(IBMQuantumError):
                await client.get_token()


class TestRequiredHeaders:
    @respx.mock
    async def test_sends_all_three_required_headers(self, settings):
        """IBM rejects calls missing IBM-API-Version -- the easiest detail to miss."""
        respx.post(IAM_URL).mock(return_value=_token_response())
        route = respx.get(f"{API_URL}/backends").mock(
            return_value=httpx.Response(200, json={"devices": []})
        )
        async with IBMQuantumClient(settings) as client:
            await client.fetch_backends()

        headers = route.calls[0].request.headers
        assert headers["authorization"] == "Bearer tok-123"
        assert headers["service-crn"] == settings.ibm_quantum_crn
        assert headers["ibm-api-version"] == "2024-01-01"


class TestBackendParsing:
    @respx.mock
    async def test_parses_ibms_nested_status_object(self, settings):
        """IBM returns status as {"name":..., "reason":...}, not a bare string."""
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/backends").mock(
            return_value=httpx.Response(
                200,
                json={
                    "devices": [
                        {
                            "name": "ibm_fez",
                            "status": {"name": "paused", "reason": "Calibrating"},
                            "queue_length": 42,
                            "qubits": 156,
                            "clops": 195000,
                            "processor_type": {"family": "Heron", "revision": 2},
                        }
                    ]
                },
            )
        )
        async with IBMQuantumClient(settings) as client:
            backends = await client.fetch_backends()

        b = backends[0]
        assert b.name == "ibm_fez"
        assert b.status is BackendStatus.PAUSED
        assert b.status_reason == "Calibrating"
        assert b.queue_length == 42
        assert b.processor_type == "Heron r2"
        # Paused devices must not advertise a wait time.
        assert b.estimated_wait_seconds == 0

    @respx.mock
    async def test_tolerates_a_flattened_status_string(self, settings):
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/backends").mock(
            return_value=httpx.Response(
                200, json={"devices": [{"name": "ibm_x", "status": "online", "n_qubits": 127}]}
            )
        )
        async with IBMQuantumClient(settings) as client:
            backends = await client.fetch_backends()
        assert backends[0].status is BackendStatus.ONLINE
        assert backends[0].qubits == 127

    @respx.mock
    async def test_accepts_alternate_envelope_keys(self, settings):
        """A future response shape should degrade, not crash."""
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/backends").mock(
            return_value=httpx.Response(200, json=[{"name": "bare_list", "status": "online"}])
        )
        async with IBMQuantumClient(settings) as client:
            assert (await client.fetch_backends())[0].name == "bare_list"


class TestJobParsing:
    @respx.mock
    async def test_parses_nested_program_usage_and_z_timestamps(self, settings):
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "cx123",
                            "backend": "ibm_torino",
                            "status": "Completed",
                            "program": {"id": "sampler"},
                            "created": "2026-09-12T10:00:00Z",
                            "usage": {"qpu_charge_time_seconds": 12.5},
                            "tags": ["vqe"],
                        },
                        {"id": "cx124", "backend": "ibm_fez", "status": "Running",
                         "created": "2026-09-12T11:00:00Z"},
                    ]
                },
            )
        )
        async with IBMQuantumClient(settings) as client:
            jobs = await client.fetch_jobs()

        assert [j.id for j in jobs] == ["cx124", "cx123"], "must be newest first"
        done = jobs[1]
        assert done.status is JobStatus.COMPLETED
        assert done.program_id == "sampler"
        assert done.qpu_charge_time_seconds == 12.5
        assert done.created.tzinfo is not None

    @respx.mock
    async def test_drops_records_with_no_id_instead_of_crashing(self, settings):
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/jobs").mock(
            return_value=httpx.Response(
                200,
                json={"jobs": [{"backend": "x", "status": "Running", "created": "2026-01-01T00:00:00Z"},
                               {"id": "ok", "backend": "y", "status": "Queued",
                                "created": "2026-01-01T00:00:00Z"}]},
            )
        )
        async with IBMQuantumClient(settings) as client:
            jobs = await client.fetch_jobs()
        assert [j.id for j in jobs] == ["ok"]


class TestErrorMapping:
    @respx.mock
    async def test_401_raises_auth_error_and_discards_the_token(self, settings):
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/backends").mock(return_value=httpx.Response(401))
        async with IBMQuantumClient(settings) as client:
            await client.get_token()
            with pytest.raises(IBMAuthError):
                await client.fetch_backends()
            # The dead token must be dropped so the next attempt re-authenticates.
            assert client._token is None

    @respx.mock
    async def test_429_explains_how_to_fix_it(self, settings):
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/backends").mock(return_value=httpx.Response(429))
        async with IBMQuantumClient(settings) as client:
            with pytest.raises(IBMQuantumError, match="POLL_INTERVAL_SECONDS"):
                await client.fetch_backends()

    @respx.mock
    async def test_503_raises_quantum_error(self, settings):
        respx.post(IAM_URL).mock(return_value=_token_response())
        respx.get(f"{API_URL}/backends").mock(return_value=httpx.Response(503, text="maintenance"))
        async with IBMQuantumClient(settings) as client:
            with pytest.raises(IBMQuantumError):
                await client.fetch_backends()
