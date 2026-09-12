"""Configuration guardrails."""

import pytest
from pydantic import ValidationError

from backend.config import Settings


class TestRateLimitFloor:
    def test_rejects_a_poll_interval_that_would_get_us_rate_limited(self):
        """IBM throttles per user; a 1-second poller is the fastest way to get blocked.

        This is enforced in code rather than left to a README warning.
        """
        with pytest.raises(ValidationError, match="rate-limit floor"):
            Settings(poll_interval_seconds=1.0)

    def test_accepts_the_documented_floor(self):
        assert Settings(poll_interval_seconds=10.0).poll_interval_seconds == 10.0


class TestModeResolution:
    def test_both_halves_of_the_credential_pair_are_required(self):
        assert not Settings(ibm_quantum_api_key="k", ibm_quantum_crn="").has_credentials
        assert not Settings(ibm_quantum_api_key="", ibm_quantum_crn="crn").has_credentials
        assert Settings(ibm_quantum_api_key="k", ibm_quantum_crn="crn").has_credentials

    def test_whitespace_only_credentials_do_not_count(self):
        assert not Settings(ibm_quantum_api_key="   ", ibm_quantum_crn="  ").has_credentials

    def test_force_mock_mode_wins_over_valid_credentials(self):
        s = Settings(ibm_quantum_api_key="k", ibm_quantum_crn="crn", force_mock_mode=True)
        assert s.has_credentials and not s.live_mode_possible

    def test_cors_origins_parse_from_a_comma_separated_string(self):
        s = Settings(cors_origins="http://a.com, http://b.com ,")
        assert s.cors_origin_list == ["http://a.com", "http://b.com"]
