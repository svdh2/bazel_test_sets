"""Unit tests for the effort SPRT rerun engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from orchestrator.execution.effort import (
    EffortClassification,
    EffortResult,
    EffortRunner,
    _classify,
)
from orchestrator.execution.executor import TestResult


class TestClassify:
    """Tests for the _classify helper."""

    def test_failed_accept_is_flake(self):
        """Initially failed + SPRT accept (reliable) -> flake."""
        c = _classify("t1", "failed", "accept", 10, 8)
        assert c.classification == "flake"
        assert c.initial_status == "failed"
        assert c.sprt_decision == "accept"

    def test_failed_reject_is_true_fail(self):
        """Initially failed + SPRT reject (unreliable) -> true_fail."""
        c = _classify("t1", "failed", "reject", 10, 2)
        assert c.classification == "true_fail"

    def test_failed_continue_is_undecided(self):
        """Initially failed + SPRT continue -> undecided."""
        c = _classify("t1", "failed", "continue", 5, 3)
        assert c.classification == "undecided"

    def test_passed_accept_is_true_pass(self):
        """Initially passed + SPRT accept (reliable) -> true_pass."""
        c = _classify("t1", "passed", "accept", 30, 30)
        assert c.classification == "true_pass"

    def test_passed_reject_is_flake(self):
        """Initially passed + SPRT reject (unreliable) -> flake."""
        c = _classify("t1", "passed", "reject", 20, 14)
        assert c.classification == "flake"

    def test_passed_continue_is_undecided(self):
        """Initially passed + SPRT continue -> undecided."""
        c = _classify("t1", "passed", "continue", 5, 4)
        assert c.classification == "undecided"

    def test_preserves_counts(self):
        """Classification preserves runs and passes."""
        c = _classify("t1", "passed", "accept", 42, 41)
        assert c.runs == 42
        assert c.passes == 41
        assert c.test_name == "t1"


class TestEffortRunnerConverge:
    """Tests for converge mode (SPRT rerun only failed tests)."""

    def test_converge_only_reruns_failed_tests(self):
        """Converge mode targets only initially-failed tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_pass": True, "t_fail": False})

            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
                TestResult(name="t_fail", assertion="b", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=5,
                effort_mode="converge",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="b", status="failed",
                )
                result = runner.run()

            # t_pass should NOT have been rerun
            called_names = [c.args[0] for c in mock_exec.call_args_list]
            assert "t_pass" not in called_names

            # t_pass classified as true_pass without evaluation
            assert result.classifications["t_pass"].classification == "true_pass"
            assert result.classifications["t_pass"].sprt_decision == "not_evaluated"

    def test_converge_true_fail_classification(self):
        """A consistently failing test is classified as true_fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_fail": False})

            initial = [
                TestResult(name="t_fail", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=50,
                effort_mode="converge",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="a", status="failed",
                )
                result = runner.run()

            c = result.classifications["t_fail"]
            assert c.classification == "true_fail"
            assert c.sprt_decision == "reject"
            assert c.passes == 0

    def test_converge_flake_classification(self):
        """A test that fails initially but mostly passes is classified as flake."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_flaky": False})

            initial = [
                TestResult(name="t_flaky", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=100,
                effort_mode="converge",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                # Mostly passes on reruns
                mock_exec.return_value = TestResult(
                    name="t_flaky", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_flaky"]
            assert c.classification == "flake"
            assert c.sprt_decision == "accept"
            assert c.passes >= 1  # at least some passes

    def test_converge_budget_exhausted_is_undecided(self):
        """Budget exhaustion produces undecided classification."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path, min_reliability=0.99)

            dag = _make_dag(tmpdir, {"t_ambig": False})

            initial = [
                TestResult(name="t_ambig", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=2,
                effort_mode="converge",
                initial_results=initial,
            )

            # All passes after initial failure: 1 fail + 2 pass = 3 runs, 2 passes
            # SPRT stays "continue" because evidence is ambiguous with so few runs
            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_ambig", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_ambig"]
            assert c.classification == "undecided"
            assert c.sprt_decision == "continue"
            assert result.total_reruns == 2

    def test_converge_records_all_reruns_in_status_file(self):
        """All reruns are recorded in the status file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_fail": False})

            initial = [
                TestResult(name="t_fail", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=50,
                effort_mode="converge",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="a", status="failed",
                )
                result = runner.run()

            # Check that status file has recorded runs
            history = sf.get_test_history("t_fail")
            assert len(history) >= result.total_reruns
            for entry in history:
                assert entry["commit"] == "abc123"

    def test_converge_skips_dependencies_failed(self):
        """Tests with dependencies_failed are not classified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_skip": False})

            initial = [
                TestResult(
                    name="t_skip", assertion="a",
                    status="dependencies_failed",
                ),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=5,
                effort_mode="converge",
                initial_results=initial,
            )

            result = runner.run()
            assert "t_skip" not in result.classifications
            assert result.total_reruns == 0


class TestEffortRunnerMax:
    """Tests for max mode (SPRT rerun all tests)."""

    def test_max_reruns_all_tests(self):
        """Max mode targets both passing and failing tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_pass": True, "t_fail": False})

            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
                TestResult(name="t_fail", assertion="b", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=50,
                effort_mode="max",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                def side_effect(name):
                    if name == "t_pass":
                        return TestResult(
                            name="t_pass", assertion="a", status="passed",
                        )
                    return TestResult(
                        name="t_fail", assertion="b", status="failed",
                    )

                mock_exec.side_effect = side_effect
                result = runner.run()

            # Both tests should have been classified via SPRT
            assert result.classifications["t_pass"].sprt_decision != "not_evaluated"
            assert result.classifications["t_fail"].sprt_decision != "not_evaluated"

    def test_max_passing_test_accept_is_true_pass(self):
        """A consistently passing test in max mode is classified as true_pass."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_pass": True})

            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_pass", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"

    def test_max_passing_test_reject_is_flake(self):
        """A passing test that starts failing on rerun is classified as flake."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_flaky": True})

            initial = [
                TestResult(name="t_flaky", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc123",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                # Mostly fails on reruns
                mock_exec.return_value = TestResult(
                    name="t_flaky", assertion="a", status="failed",
                )
                result = runner.run()

            c = result.classifications["t_flaky"]
            assert c.classification == "flake"
            assert c.sprt_decision == "reject"


class TestEffortRunnerSessionOnly:
    """Tests that SPRT uses only session-local data (no target_hashes)."""

    def test_no_historic_data_used(self):
        """Pre-existing status file history is not used for SPRT evaluation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with old history (many failures on a different commit)
            for _ in range(50):
                sf.record_run("t_pass", False, commit="old_commit")
            sf.save()

            dag = _make_dag(tmpdir, {"t_pass": True})

            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="new_commit",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_pass", assertion="a", status="passed",
                )
                result = runner.run()

            # Despite old failures, session shows all passes -> true_pass
            c = result.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"

    def test_no_target_hashes_ignores_prior_same_hash_history(self):
        """Without target_hashes, same-hash history in status file is ignored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with many same-hash failures -- these should be
            # ignored because target_hashes is None
            sf.set_test_state("t_pass", "burning_in")
            for _ in range(50):
                sf.record_run(
                    "t_pass", False, commit="old",
                    target_hash="hash_a",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_pass": True})
            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="new",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes=None,  # explicitly None
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_pass", assertion="a", status="passed",
                )
                result = runner.run()

            # Session-only: 1 initial pass + all rerun passes -> true_pass
            c = result.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"


class TestEffortRunnerSameHashPooling:
    """Tests for same-hash evidence pooling with target_hashes."""

    def test_prior_passes_speed_up_accept(self):
        """Prior same-hash passes help SPRT reach 'accept' faster."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with many same-hash passes from a prior session
            sf.set_test_state("t_pass", "burning_in")
            for _ in range(30):
                sf.record_run(
                    "t_pass", True, commit="prior",
                    target_hash="hash_a",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_pass": True})
            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"t_pass": "hash_a"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_pass", assertion="a", status="passed",
                )
                result = runner.run()

            # With 30 prior passes + 1 initial pass = 31 total passes,
            # SPRT should accept with very few (or zero) reruns
            c = result.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"
            # The prior evidence should mean fewer reruns needed
            assert result.total_reruns <= 5

    def test_prior_passes_enable_immediate_accept(self):
        """Enough prior same-hash evidence can produce immediate acceptance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with enough same-hash passes for immediate SPRT accept
            sf.set_test_state("t_pass", "burning_in")
            for _ in range(50):
                sf.record_run(
                    "t_pass", True, commit="prior",
                    target_hash="hash_a",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_pass": True})
            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"t_pass": "hash_a"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                result = runner.run()

            # 50 prior + 1 initial = 51 total -> immediate accept, zero reruns
            c = result.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"
            assert result.total_reruns == 0
            mock_exec.assert_not_called()

    def test_prior_failures_speed_up_reject(self):
        """Prior same-hash failures help SPRT reach 'reject' faster."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with many same-hash failures
            sf.set_test_state("t_fail", "burning_in")
            for _ in range(30):
                sf.record_run(
                    "t_fail", False, commit="prior",
                    target_hash="hash_b",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_fail": False})
            initial = [
                TestResult(name="t_fail", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="converge",
                initial_results=initial,
                target_hashes={"t_fail": "hash_b"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="a", status="failed",
                )
                result = runner.run()

            c = result.classifications["t_fail"]
            assert c.classification == "true_fail"
            assert c.sprt_decision == "reject"
            # Prior evidence should speed up rejection
            assert result.total_reruns <= 5

    def test_different_hash_not_pooled(self):
        """Prior history with a different hash is NOT pooled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with OLD hash history (many passes)
            sf.set_test_state("t_pass", "burning_in")
            for _ in range(50):
                sf.record_run(
                    "t_pass", True, commit="prior",
                    target_hash="old_hash",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_pass": True})
            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            # Current hash is different from stored history hash
            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"t_pass": "new_hash"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_pass", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"
            # Without pooled evidence, it should take more reruns
            # (the prior evidence with old_hash is not counted)
            assert result.total_reruns > 0

    def test_mixed_hash_history_only_pools_matching(self):
        """Only history entries with matching hash are pooled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            sf.set_test_state("t_test", "burning_in")
            # Add entries with old hash (should be excluded)
            for _ in range(20):
                sf.record_run(
                    "t_test", False, commit="old",
                    target_hash="old_hash",
                )
            # Add entries with current hash (should be included)
            for _ in range(30):
                sf.record_run(
                    "t_test", True, commit="recent",
                    target_hash="current_hash",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_test": True})
            initial = [
                TestResult(name="t_test", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="now",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"t_test": "current_hash"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_test", assertion="a", status="passed",
                )
                result = runner.run()

            # 30 prior matching passes + 1 initial pass -> should accept fast
            c = result.classifications["t_test"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"
            # Should be fast since 31 same-hash passes
            assert result.total_reruns <= 5

    def test_no_hash_for_test_uses_session_only(self):
        """If test has no entry in target_hashes, session-only is used."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with same-hash history
            sf.set_test_state("t_no_hash", "burning_in")
            for _ in range(50):
                sf.record_run(
                    "t_no_hash", True, commit="prior",
                    target_hash="some_hash",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_no_hash": True})
            initial = [
                TestResult(name="t_no_hash", assertion="a", status="passed"),
            ]

            # target_hashes provided but doesn't include "t_no_hash"
            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"other_test": "hash_x"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_no_hash", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_no_hash"]
            assert c.classification == "true_pass"
            # Should need reruns since no prior evidence pooled
            assert result.total_reruns > 0


class TestEffortRunnerRecordRunWithHash:
    """Tests that record_run includes target_hash in history entries."""

    def test_target_hash_stored_in_history(self):
        """Each rerun records the target hash in the history entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_fail": False})
            initial = [
                TestResult(name="t_fail", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc",
                max_reruns=3,
                effort_mode="converge",
                initial_results=initial,
                target_hashes={"t_fail": "hash_xyz"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="a", status="failed",
                )
                runner.run()

            # Check history entries for target_hash
            history = sf.get_test_history("t_fail")
            rerun_entries = [h for h in history if h.get("commit") == "abc"]
            for entry in rerun_entries:
                assert entry.get("target_hash") == "hash_xyz"

    def test_no_target_hash_when_not_provided(self):
        """Without target_hashes, no target_hash in history entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_fail": False})
            initial = [
                TestResult(name="t_fail", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc",
                max_reruns=3,
                effort_mode="converge",
                initial_results=initial,
                # No target_hashes
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="a", status="failed",
                )
                runner.run()

            # History entries should NOT have target_hash
            history = sf.get_test_history("t_fail")
            rerun_entries = [h for h in history if h.get("commit") == "abc"]
            for entry in rerun_entries:
                assert "target_hash" not in entry

    def test_target_hash_stored_even_when_test_not_in_hashes(self):
        """Test not in target_hashes dict records no hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            dag = _make_dag(tmpdir, {"t_fail": False})
            initial = [
                TestResult(name="t_fail", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc",
                max_reruns=3,
                effort_mode="converge",
                initial_results=initial,
                target_hashes={"other_test": "hash_a"},  # t_fail not included
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_fail", assertion="a", status="failed",
                )
                runner.run()

            history = sf.get_test_history("t_fail")
            rerun_entries = [h for h in history if h.get("commit") == "abc"]
            for entry in rerun_entries:
                assert "target_hash" not in entry

    def test_converge_pooling_flake_detection(self):
        """Prior same-hash passes + current initial failure + rerun passes -> flake.

        A test with extensive prior same-hash passing evidence fails once
        in the current session but passes on all reruns.  The pooled
        evidence (many prior passes + reruns) should be enough for SPRT
        to accept (reliable), which combined with initial_status=failed
        yields a "flake" classification.

        Uses a lower min_reliability threshold so that a single failure
        among many passes doesn't push past the boundary.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            # Use lower min_reliability=0.90 so one failure among many
            # passes still evaluates as "reliable" (accept)
            sf = _make_status_file(
                status_path, min_reliability=0.90, significance=0.95,
            )

            # Prior session: 50 all-passing runs with this hash
            sf.set_test_state("t_flaky", "burning_in")
            for _ in range(50):
                sf.record_run(
                    "t_flaky", True, commit="prior",
                    target_hash="hash_f",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_flaky": True})
            # Current session: test fails initially
            initial = [
                TestResult(name="t_flaky", assertion="a", status="failed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="converge",
                initial_results=initial,
                target_hashes={"t_flaky": "hash_f"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                # Passes on all reruns
                mock_exec.return_value = TestResult(
                    name="t_flaky", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_flaky"]
            # 50 prior passes + 1 initial failure + passes on reruns
            # -> mostly passing -> SPRT accepts (reliable)
            # -> initial_status="failed" + accept -> "flake"
            assert c.classification == "flake"
            assert c.sprt_decision == "accept"

    def test_classification_runs_include_prior_evidence(self):
        """The runs/passes counts in classification reflect pooled evidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Pre-populate with 20 same-hash passes
            sf.set_test_state("t_test", "burning_in")
            for _ in range(20):
                sf.record_run(
                    "t_test", True, commit="prior",
                    target_hash="hash_t",
                )
            sf.save()

            dag = _make_dag(tmpdir, {"t_test": True})
            initial = [
                TestResult(name="t_test", assertion="a", status="passed"),
            ]

            runner = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="current",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"t_test": "hash_t"},
            )

            with patch.object(runner, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_test", assertion="a", status="passed",
                )
                result = runner.run()

            c = result.classifications["t_test"]
            # Runs should include prior evidence (20) + initial (1) + reruns
            assert c.runs >= 21
            assert c.passes >= 21

    def test_empty_prior_history_same_as_no_target_hashes(self):
        """target_hashes provided but no matching history behaves like session-only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = _make_status_file(status_path)

            # Fresh status file -- no history at all
            dag = _make_dag(tmpdir, {"t_pass": True})
            initial = [
                TestResult(name="t_pass", assertion="a", status="passed"),
            ]

            runner_with_hash = EffortRunner(
                dag=dag,
                status_file=sf,
                commit_sha="abc",
                max_reruns=100,
                effort_mode="max",
                initial_results=initial,
                target_hashes={"t_pass": "hash_new"},
            )

            with patch.object(runner_with_hash, "_execute_test") as mock_exec:
                mock_exec.return_value = TestResult(
                    name="t_pass", assertion="a", status="passed",
                )
                result_with = runner_with_hash.run()

            # Should behave the same as without target_hashes since
            # there's no prior same-hash history to pool
            c = result_with.classifications["t_pass"]
            assert c.classification == "true_pass"
            assert c.sprt_decision == "accept"
            assert result_with.total_reruns > 0  # needed reruns


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_status_file(
    path: Path,
    min_reliability: float = 0.99,
    significance: float = 0.95,
) -> Any:
    """Create a StatusFile with optional config overrides."""
    from orchestrator.lifecycle.status import StatusFile

    sf = StatusFile(
        path,
        min_reliability=min_reliability,
        statistical_significance=significance,
    )
    sf.save()
    return sf


def _make_dag(
    tmpdir: str,
    tests: dict[str, bool],
) -> Any:
    """Build a minimal TestDAG from a {name: passes} dict.

    Creates shell scripts that exit 0 (pass) or 1 (fail) and a manifest.
    """
    import stat
    from orchestrator.execution.dag import TestDAG

    manifest: dict = {"test_set": {"name": "tests"}, "test_set_tests": {}}
    for name, passes in tests.items():
        script = Path(tmpdir) / f"{name}.sh"
        script.write_text(f"#!/bin/bash\nexit {'0' if passes else '1'}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        manifest["test_set_tests"][name] = {
            "assertion": name,
            "executable": str(script),
            "depends_on": [],
        }

    return TestDAG.from_manifest(manifest)
