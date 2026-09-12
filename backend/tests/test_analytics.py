"""The recommender is a decision tool, so its ordering has to be defensible."""

from backend.core.analytics import build_summary, recommend_backends
from backend.models import BackendStatus, JobStatus, QPUBackend, QuantumJob
from datetime import datetime, timezone


def _backend(name, status=BackendStatus.ONLINE, queue=50, qubits=127, clops=30_000, sim=False):
    return QPUBackend(name=name, status=status, queue_length=queue, qubits=qubits,
                      clops=clops, is_simulator=sim)


class TestRecommender:
    def test_prefers_the_shorter_queue_all_else_equal(self):
        result = recommend_backends([_backend("busy", queue=400), _backend("quiet", queue=5)])
        assert result[0].backend_name == "quiet"
        assert result[0].rank == 1

    def test_prefers_more_qubits_all_else_equal(self):
        result = recommend_backends([_backend("small", qubits=27), _backend("large", qubits=156)])
        assert result[0].backend_name == "large"

    def test_prefers_the_faster_processor_all_else_equal(self):
        result = recommend_backends([_backend("slow", clops=27_000), _backend("fast", clops=195_000)])
        assert result[0].backend_name == "fast"

    def test_queue_depth_outweighs_raw_qubit_count(self):
        """A 156-qubit machine with 500 people ahead of you is the wrong answer."""
        result = recommend_backends([
            _backend("big_but_swamped", queue=500, qubits=156, clops=195_000),
            _backend("smaller_but_free", queue=2, qubits=127, clops=32_000),
        ])
        assert result[0].backend_name == "smaller_but_free"

    def test_excludes_machines_that_cannot_accept_jobs(self):
        names = [r.backend_name for r in recommend_backends([
            _backend("paused", status=BackendStatus.PAUSED, queue=0),
            _backend("offline", status=BackendStatus.OFFLINE, queue=0),
            _backend("online", queue=99),
        ])]
        assert names == ["online"]

    def test_excludes_simulators_by_default_but_can_include_them(self):
        fleet = [_backend("sim", sim=True, queue=0), _backend("real", queue=80)]
        assert [r.backend_name for r in recommend_backends(fleet)] == ["real"]
        assert len(recommend_backends(fleet, include_simulators=True)) == 2

    def test_min_qubits_filters_out_circuits_that_would_not_fit(self):
        fleet = [_backend("small", qubits=27, queue=0), _backend("large", qubits=156, queue=90)]
        result = recommend_backends(fleet, min_qubits=100)
        assert [r.backend_name for r in result] == ["large"]

    def test_returns_nothing_rather_than_a_bad_answer(self):
        assert recommend_backends([]) == []
        assert recommend_backends([_backend("down", status=BackendStatus.OFFLINE)]) == []

    def test_ranks_are_sequential_and_scores_descend(self):
        result = recommend_backends(
            [_backend(f"b{i}", queue=i * 40) for i in range(5)], limit=3
        )
        assert [r.rank for r in result] == [1, 2, 3]
        assert result[0].score >= result[1].score >= result[2].score

    def test_every_recommendation_explains_itself(self):
        """A score with no reasoning is a number, not a recommendation."""
        for rec in recommend_backends([_backend("a", queue=5), _backend("b", queue=300)]):
            assert rec.rationale and rec.rationale[0].isupper() and rec.rationale.endswith(".")

    def test_rationale_preserves_the_clops_acronym(self):
        rec = recommend_backends([_backend("fast", clops=195_000), _backend("slow", clops=1_000)])[0]
        assert "CLOPS" in rec.rationale

    def test_identical_backends_do_not_divide_by_zero(self):
        result = recommend_backends([_backend("a"), _backend("b"), _backend("c")])
        assert len(result) == 3 and all(0 <= r.score <= 100 for r in result)


class TestFleetSummary:
    def test_counts_each_status_independently(self):
        summary = build_summary([
            _backend("a"), _backend("b"),
            _backend("c", status=BackendStatus.PAUSED),
            _backend("d", status=BackendStatus.OFFLINE),
        ], [])
        assert (summary.total_backends, summary.online_backends,
                summary.paused_backends, summary.offline_backends) == (4, 2, 1, 1)

    def test_available_qubits_excludes_simulators_and_down_machines(self):
        summary = build_summary([
            _backend("online", qubits=156),
            _backend("paused", qubits=127, status=BackendStatus.PAUSED),
            _backend("sim", qubits=32, sim=True),
        ], [])
        assert summary.total_qubits_available == 156

    def test_jobs_in_flight_counts_only_unfinished_work(self):
        now = datetime.now(timezone.utc)
        jobs = [
            QuantumJob(id="1", backend="x", status=JobStatus.QUEUED, created=now),
            QuantumJob(id="2", backend="x", status=JobStatus.RUNNING, created=now),
            QuantumJob(id="3", backend="x", status=JobStatus.COMPLETED, created=now),
            QuantumJob(id="4", backend="x", status=JobStatus.FAILED, created=now),
        ]
        assert build_summary([_backend("x")], jobs).jobs_in_flight == 2

    def test_identifies_busiest_and_quietest(self):
        summary = build_summary(
            [_backend("busy", queue=500), _backend("quiet", queue=1)], []
        )
        assert summary.busiest_backend == "busy"
        assert summary.quietest_operational_backend == "quiet"

    def test_empty_fleet_returns_zeros_rather_than_exploding(self):
        assert build_summary([], []).total_backends == 0
