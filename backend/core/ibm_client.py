"""Typed async client for the IBM Quantum Compute Service REST API.

Grounded in the published contract (verified against IBM's docs, not assumed):

  * Token endpoint  ``POST https://iam.cloud.ibm.com/identity/token``
    with ``grant_type=urn:ibm:params:oauth:grant-type:apikey``; the response
    carries ``access_token`` and ``expires_in`` (3600 seconds).
  * Base URL        ``https://quantum.cloud.ibm.com/api/v1``
                    (EU region: ``https://eu-de.quantum.cloud.ibm.com/api/v1``)
  * Every call requires three headers, not two::

        Authorization:   Bearer <token>
        Service-CRN:     <instance CRN>
        IBM-API-Version: 2024-01-01

    Omitting ``IBM-API-Version`` is rejected -- a detail that is easy to miss.
  * ``GET /backends`` returns ``devices[]`` with ``name``, ``status``
    (an *object* of ``{name, reason}``, not a string), ``queue_length``,
    ``qubits`` and ``clops``.
  * ``GET /jobs`` returns ``jobs[]`` with ``id``, ``backend``, ``status``
    (``Queued``/``Running``/``Completed``/``Cancelled``/``Failed``),
    ``program.id``, ``created``, ``session_id`` and ``usage``.

Token handling is *proactive*: we refresh a few minutes before stated expiry
rather than waiting for a 401 and retrying. That removes an entire class of
transient failure from the poller's hot path.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from ..config import Settings
from ..models import BackendStatus, JobStatus, QPUBackend, QuantumJob

logger = logging.getLogger(__name__)


class IBMQuantumError(RuntimeError):
    """Any failure that should push the poller onto its mock fallback."""


class IBMAuthError(IBMQuantumError):
    """Credentials were rejected -- retrying without new credentials won't help."""


class IBMQuantumClient:
    """Async client owning one HTTP connection pool and one cached IAM token."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            # A small pool is plenty: exactly one poller uses this client.
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        # Serialises concurrent refreshes so a burst of callers triggers one
        # IAM round-trip, not N of them.
        self._token_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------
    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "IBMQuantumClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # -- authentication ------------------------------------------------------
    @property
    def _token_is_fresh(self) -> bool:
        if not self._token or not self._token_expires_at:
            return False
        margin = timedelta(seconds=self._settings.token_refresh_margin_seconds)
        return datetime.now(timezone.utc) + margin < self._token_expires_at

    async def get_token(self) -> str:
        """Return a valid IAM bearer token, refreshing it ahead of expiry."""
        if self._token_is_fresh:
            return self._token  # type: ignore[return-value]

        async with self._token_lock:
            # Re-check: another coroutine may have refreshed while we waited.
            if self._token_is_fresh:
                return self._token  # type: ignore[return-value]

            if not self._settings.ibm_quantum_api_key:
                raise IBMAuthError("IBM_QUANTUM_API_KEY is not set.")

            try:
                response = await self._client.post(
                    self._settings.ibm_iam_url,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                    },
                    data={
                        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                        "apikey": self._settings.ibm_quantum_api_key,
                    },
                )
            except httpx.HTTPError as exc:
                raise IBMQuantumError(f"Could not reach IBM IAM: {exc}") from exc

            if response.status_code in (400, 401, 403):
                raise IBMAuthError(
                    f"IBM IAM rejected the API key (HTTP {response.status_code}). "
                    "Check IBM_QUANTUM_API_KEY."
                )
            if response.status_code >= 400:
                raise IBMQuantumError(
                    f"IBM IAM returned HTTP {response.status_code}: {response.text[:200]}"
                )

            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise IBMQuantumError("IBM IAM response contained no access_token.")

            expires_in = int(payload.get("expires_in", 3600))
            self._token = token
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            logger.info("Obtained IAM token; valid for %s seconds.", expires_in)
            return token

    async def _auth_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {await self.get_token()}",
            "Service-CRN": self._settings.ibm_quantum_crn,
            "IBM-API-Version": self._settings.ibm_api_version,
        }

    # -- requests ------------------------------------------------------------
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._settings.ibm_quantum_api_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = await self._client.get(url, headers=await self._auth_headers(), params=params)
        except httpx.HTTPError as exc:
            raise IBMQuantumError(f"GET {path} failed: {exc}") from exc

        if response.status_code == 401:
            # Token was rejected mid-flight. Drop it so the next attempt
            # re-authenticates from scratch rather than reusing a dead token.
            self._token = None
            self._token_expires_at = None
            raise IBMAuthError(f"IBM rejected the bearer token on GET {path}.")
        if response.status_code == 429:
            raise IBMQuantumError(
                f"Rate limited by IBM on GET {path}. "
                "Increase POLL_INTERVAL_SECONDS."
            )
        if response.status_code >= 400:
            raise IBMQuantumError(
                f"GET {path} returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise IBMQuantumError(f"GET {path} returned non-JSON content.") from exc

    # -- parsing -------------------------------------------------------------
    @staticmethod
    def _parse_backend(raw: dict[str, Any]) -> QPUBackend:
        """Map one IBM device object onto our domain type.

        IBM nests operational state as ``{"status": {"name": ..., "reason": ...}}``
        but some responses flatten it to a bare string, so both are handled.
        """
        status_field = raw.get("status")
        if isinstance(status_field, dict):
            status = BackendStatus.parse(status_field.get("name"))
            reason = status_field.get("reason") or None
        else:
            status = BackendStatus.parse(status_field)
            reason = raw.get("status_reason") or None

        qubits = raw.get("qubits")
        if qubits is None:
            qubits = raw.get("n_qubits", 0)

        return QPUBackend(
            name=raw.get("name") or raw.get("backend_name") or "unknown",
            status=status,
            status_reason=reason,
            queue_length=int(raw.get("queue_length") or 0),
            qubits=int(qubits or 0),
            clops=raw.get("clops"),
            is_simulator=bool(raw.get("is_simulator", False)),
            processor_type=IBMQuantumClient._parse_processor_type(raw),
        )

    @staticmethod
    def _parse_processor_type(raw: dict[str, Any]) -> str | None:
        """IBM reports processor family as either a string or a {family, revision} object."""
        value = raw.get("processor_type")
        if isinstance(value, dict):
            family = value.get("family")
            revision = value.get("revision")
            if family and revision:
                return f"{family} r{revision}"
            return family or None
        return value or None

    @staticmethod
    def _parse_job(raw: dict[str, Any]) -> QuantumJob | None:
        job_id = raw.get("id")
        if not job_id:
            return None

        created_raw = raw.get("created")
        try:
            created = (
                datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
                if created_raw
                else datetime.now(timezone.utc)
            )
        except ValueError:
            created = datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        program = raw.get("program")
        program_id = program.get("id") if isinstance(program, dict) else program

        usage = raw.get("usage")
        charge_time = (
            usage.get("qpu_charge_time_seconds") if isinstance(usage, dict) else None
        )

        return QuantumJob(
            id=str(job_id),
            backend=str(raw.get("backend") or "unknown"),
            status=JobStatus.parse(raw.get("status")),
            program_id=str(program_id) if program_id else None,
            created=created,
            session_id=raw.get("session_id"),
            qpu_charge_time_seconds=charge_time,
            tags=list(raw.get("tags") or []),
        )

    # -- public API ----------------------------------------------------------
    async def fetch_backends(self) -> list[QPUBackend]:
        payload = await self._get("/backends")
        # IBM documents the array as `devices`; tolerate `backends` and a bare
        # list so a future response-shape tweak degrades rather than crashes.
        if isinstance(payload, list):
            raw_devices = payload
        else:
            raw_devices = payload.get("devices") or payload.get("backends") or []
        return [self._parse_backend(d) for d in raw_devices if isinstance(d, dict)]

    async def fetch_jobs(self, limit: int = 50) -> list[QuantumJob]:
        payload = await self._get("/jobs", params={"limit": limit, "sort": "DESC"})
        raw_jobs = payload if isinstance(payload, list) else (payload.get("jobs") or [])
        jobs = [self._parse_job(j) for j in raw_jobs if isinstance(j, dict)]
        return sorted(
            (job for job in jobs if job is not None),
            key=lambda job: job.created,
            reverse=True,
        )
