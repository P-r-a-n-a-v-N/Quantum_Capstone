"""Application configuration, loaded from the environment / .env file.

Everything the dashboard needs to switch between Live and Simulated telemetry is
expressed here, so no other module has to reach for ``os.environ``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the repository root (one level above backend/) so the
# app behaves identically whether it is started from the repo root or backend/.
_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- IBM credentials ----------------------------------------------------
    ibm_quantum_api_key: str = ""
    ibm_quantum_crn: str = ""

    # --- IBM endpoints ------------------------------------------------------
    ibm_quantum_api_url: str = "https://quantum.cloud.ibm.com/api/v1"
    ibm_iam_url: str = "https://iam.cloud.ibm.com/identity/token"
    # IBM rejects requests without this header. Pinned, not floating.
    ibm_api_version: str = "2024-01-01"

    # --- Poller -------------------------------------------------------------
    # IBM rate-limits aggressively; 10s is the documented floor we hold ourselves
    # to, and the validator below refuses to let anyone configure faster.
    poll_interval_seconds: float = 12.0
    http_timeout_seconds: float = 15.0
    # Refresh the IAM token this many seconds before its stated expiry, so a
    # request is never made with a token that expires mid-flight.
    token_refresh_margin_seconds: int = 300

    force_mock_mode: bool = False

    # --- History ------------------------------------------------------------
    enable_history: bool = True
    history_db_path: str = "./data/telemetry.sqlite3"
    history_retention_days: int = 7

    # --- Serving ------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("poll_interval_seconds")
    @classmethod
    def _enforce_rate_limit_floor(cls, value: float) -> float:
        """Guard against a well-meaning edit that would get us rate-limited.

        IBM's Runtime API throttles per-user. Polling faster than every 10s is
        the single easiest way to get this dashboard blocked, so the floor is
        enforced in code rather than left to documentation.
        """
        if value < 10.0:
            raise ValueError(
                f"poll_interval_seconds={value} violates the IBM rate-limit floor "
                "of 10 seconds. Raise POLL_INTERVAL_SECONDS to 10 or more."
            )
        return value

    @property
    def has_credentials(self) -> bool:
        """True only when both halves of IBM's auth pair are present."""
        return bool(self.ibm_quantum_api_key.strip() and self.ibm_quantum_crn.strip())

    @property
    def live_mode_possible(self) -> bool:
        return self.has_credentials and not self.force_mock_mode

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def resolved_history_path(self) -> Path:
        path = Path(self.history_db_path)
        return path if path.is_absolute() else (_REPO_ROOT / "backend" / path).resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor so settings are parsed exactly once per process."""
    return Settings()
