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
    """Tests that SPRT uses only session-local data."""

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
