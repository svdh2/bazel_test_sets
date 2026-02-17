"""Unit tests for the burn-in module."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from orchestrator.lifecycle.burnin import (
    BurnInSweep,
    filter_tests_by_state,
    handle_stable_failure,
    process_results,
    sync_disabled_state,
)
from orchestrator.execution.dag import TestDAG
from orchestrator.execution.executor import TestResult
from orchestrator.lifecycle.status import StatusFile


def _make_script(content: str) -> str:
    """Create a temporary executable script."""
    fd, path = tempfile.mkstemp(suffix=".sh")
    os.write(fd, content.encode())
    os.close(fd)
    os.chmod(path, stat.S_IRWXU)
    return path


def _make_pass_script() -> str:
    return _make_script("#!/bin/bash\nexit 0\n")


def _make_fail_script() -> str:
    return _make_script("#!/bin/bash\nexit 1\n")


def _make_manifest(test_specs: dict) -> dict:
    return {
        "test_set": {
            "name": "test_root",
            "assertion": "test",
            "tests": list(test_specs.keys()),
            "subsets": [],
        },
        "test_set_tests": {
            name: {
                "assertion": spec.get("assertion", f"{name} works"),
                "executable": spec["executable"],
                "depends_on": spec.get("depends_on", []),
            }
            for name, spec in test_specs.items()
        },
    }


class TestBurnInSweepToStable:
    """Tests for burn-in sweep transitioning tests to stable."""

    def test_to_stable_all_pass(self):
        """A test that always passes should transition to stable."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                status_path = Path(tmpdir) / "status.json"
                sf = StatusFile(status_path)
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf, max_iterations=200)
                result = sweep.run()

                assert "a" in result.decided
                assert result.decided["a"] == "stable"
                assert result.undecided == []
                assert result.total_runs > 0

                # Verify state file updated
                sf2 = StatusFile(status_path)
                assert sf2.get_test_state("a") == "stable"
        finally:
            os.unlink(pass_exe)


class TestBurnInSweepToFlaky:
    """Tests for burn-in sweep transitioning tests to flaky."""

    def test_to_flaky_all_fail(self):
        """A test that always fails should transition to flaky."""
        fail_exe = _make_fail_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": fail_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                status_path = Path(tmpdir) / "status.json"
                sf = StatusFile(status_path)
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf, max_iterations=200)
                result = sweep.run()

                assert "a" in result.decided
                assert result.decided["a"] == "flaky"
                assert result.total_runs > 0

                sf2 = StatusFile(status_path)
                assert sf2.get_test_state("a") == "flaky"
        finally:
            os.unlink(fail_exe)


class TestBurnInSweepMultiple:
    """Tests for sweeping multiple tests."""

    def test_sweep_multiple_tests(self):
        """Multiple tests can be swept simultaneously."""
        pass_exe = _make_pass_script()
        fail_exe = _make_fail_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": fail_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.set_test_state("b", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf, max_iterations=200)
                result = sweep.run()

                assert result.decided["a"] == "stable"
                assert result.decided["b"] == "flaky"
        finally:
            os.unlink(pass_exe)
            os.unlink(fail_exe)

    def test_sweep_skips_non_burning_in(self):
        """Sweep only runs burning_in tests."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.set_test_state("b", "stable", runs=50, passes=50)
                sf.save()

                sweep = BurnInSweep(dag, sf)
                result = sweep.run()

                # Only a should be decided
                assert "a" in result.decided
                assert "b" not in result.decided
        finally:
            os.unlink(pass_exe)


class TestBurnInSweepSpecific:
    """Tests for sweeping specific tests."""

    def test_sweep_specific_tests(self):
        """Can specify which tests to sweep."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.set_test_state("b", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf)
                result = sweep.run(test_names=["a"])

                assert "a" in result.decided
                assert "b" not in result.decided
        finally:
            os.unlink(pass_exe)


class TestBurnInCrashRecovery:
    """Tests for incremental state file saves."""

    def test_state_file_updated_after_each_run(self):
        """State file is updated after each run for crash recovery."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                status_path = Path(tmpdir) / "status.json"
                sf = StatusFile(status_path)
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf, max_iterations=200)
                sweep.run()

                # After sweep, state file should exist and be valid
                sf2 = StatusFile(status_path)
                entry = sf2.get_test_entry("a")
                assert entry is not None
                assert entry["runs"] > 0
        finally:
            os.unlink(pass_exe)


class TestStableDemotion:
    """Tests for stable test demotion logic."""

    def test_demotion_on_repeated_failures(self):
        """Repeatedly failing test is demoted from stable to flaky."""
        fail_exe = _make_fail_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": fail_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable", runs=50, passes=50)
                sf.save()

                result = handle_stable_failure(
                    "a", dag, sf, commit_sha="abc123", max_reruns=20
                )
                assert result == "demote"
                assert sf.get_test_state("a") == "flaky"
        finally:
            os.unlink(fail_exe)

    def test_retention_on_one_off_failure(self):
        """Test that passes on re-run is retained as stable."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable", runs=50, passes=50)
                sf.save()

                result = handle_stable_failure("a", dag, sf, max_reruns=30)
                assert result == "retain"
                assert sf.get_test_state("a") == "stable"
        finally:
            os.unlink(pass_exe)

    def test_demotion_nonexistent_test(self):
        """Demotion for test not in DAG returns inconclusive."""
        dag = TestDAG()
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            result = handle_stable_failure("nonexistent", dag, sf)
            assert result == "inconclusive"

    def test_demotion_records_commit_in_history(self):
        """handle_stable_failure records commit SHA in history."""
        fail_exe = _make_fail_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": fail_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable", runs=50, passes=50)
                sf.save()

                handle_stable_failure(
                    "a", dag, sf, commit_sha="deadbeef", max_reruns=20
                )
                history = sf.get_test_history("a")
                assert len(history) > 0
                assert all(h["commit"] == "deadbeef" for h in history)
        finally:
            os.unlink(fail_exe)

    def test_demotion_uses_persisted_history(self):
        """Demotion considers pre-existing history from previous CI runs.

        Simulates cross-run demotion: the test has accumulated failures
        from prior runs. A single additional failure in the current session
        (combined with the persisted history) should trigger demotion.
        """
        fail_exe = _make_fail_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": fail_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable", runs=60, passes=55)

                # Pre-populate with a history of recent failures from
                # previous CI runs (newest-first).
                for i in range(8):
                    sf.record_run("a", passed=False, commit=f"prev_{i}")
                sf.save()

                # Now handle_stable_failure adds one more failure via
                # the fail script, reads the full persisted history, and
                # should demote quickly.
                result = handle_stable_failure(
                    "a", dag, sf, commit_sha="current", max_reruns=5
                )
                assert result == "demote"
                assert sf.get_test_state("a") == "flaky"
        finally:
            os.unlink(fail_exe)


class TestBurnInSweepCommitSHA:
    """Tests for commit SHA propagation in burn-in sweep."""

    def test_sweep_records_commit_in_history(self):
        """Burn-in sweep records commit SHA in history entries."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf, commit_sha="abc123")
                sweep.run()

                history = sf.get_test_history("a")
                assert len(history) > 0
                assert all(h["commit"] == "abc123" for h in history)
        finally:
            os.unlink(pass_exe)

    def test_sweep_without_commit_records_none(self):
        """Burn-in sweep without commit SHA records None."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "burning_in", runs=0, passes=0)
                sf.save()

                sweep = BurnInSweep(dag, sf)
                sweep.run()

                history = sf.get_test_history("a")
                assert len(history) > 0
                assert all(h["commit"] is None for h in history)
        finally:
            os.unlink(pass_exe)


class TestFilterTestsByState:
    """Tests for filtering tests by burn-in state."""

    def test_filter_stable_only(self):
        """Default filter includes only stable tests."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": pass_exe, "depends_on": []},
                "c": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable")
                sf.set_test_state("b", "burning_in")
                sf.set_test_state("c", "flaky")
                sf.save()

                result = filter_tests_by_state(dag, sf)
                assert result == ["a"]
        finally:
            os.unlink(pass_exe)

    def test_filter_includes_unknown_as_stable(self):
        """Tests not in status file are treated as stable."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable")
                # b is not in status file
                sf.save()

                result = filter_tests_by_state(dag, sf)
                assert sorted(result) == ["a", "b"]
        finally:
            os.unlink(pass_exe)

    def test_filter_custom_states(self):
        """Custom state filter works."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": pass_exe, "depends_on": []},
                "c": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "burning_in")
                sf.set_test_state("b", "flaky")
                sf.set_test_state("c", "stable")
                sf.save()

                result = filter_tests_by_state(
                    dag, sf, include_states={"burning_in", "flaky"}
                )
                assert sorted(result) == ["a", "b"]
        finally:
            os.unlink(pass_exe)

    def test_filter_empty_dag(self):
        """Empty DAG returns empty list."""
        dag = TestDAG()
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            result = filter_tests_by_state(dag, sf)
            assert result == []


def _result(name: str, status: str = "passed") -> TestResult:
    """Create a minimal TestResult for process_results tests."""
    return TestResult(name=name, assertion=f"{name} works", status=status)


class TestProcessResultsNormalOps:
    """Tests for process_results recording results (normal operation)."""

    def test_records_passing_result(self):
        """Passing test is recorded in status file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            results = [_result("a", "passed")]
            events = process_results(results, sf)

            assert events == []
            entry = sf.get_test_entry("a")
            assert entry is not None
            assert entry["runs"] == 1
            assert entry["passes"] == 1

    def test_skips_dependencies_failed(self):
        """Tests with dependencies_failed are not recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            results = [_result("a", "dependencies_failed")]
            events = process_results(results, sf)

            assert events == []
            assert sf.get_test_entry("a") is None

    def test_new_test_created_as_new(self):
        """Test not in status file is created with state 'new'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            results = [_result("a", "passed")]
            process_results(results, sf)

            assert sf.get_test_state("a") == "new"

    def test_flaky_test_just_records(self):
        """Flaky test result is recorded without state transition."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "flaky", runs=30, passes=20)
            sf.save()
            results = [_result("a", "failed")]
            events = process_results(results, sf)

            assert events == []
            assert sf.get_test_state("a") == "flaky"
            assert sf.get_test_entry("a")["runs"] == 31

    def test_commit_sha_propagated(self):
        """Commit SHA is recorded in history entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            results = [_result("a", "passed")]
            process_results(results, sf, commit_sha="abc123")

            history = sf.get_test_history("a")
            assert len(history) == 1
            assert history[0]["commit"] == "abc123"


class TestProcessResultsBurnIn:
    """Tests for process_results handling burning_in tests."""

    def test_burning_in_accepted(self):
        """Burning-in test with enough passes is accepted as stable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "burning_in", runs=19, passes=19)
            for _ in range(19):
                sf.record_run("a", True)
            sf.save()

            results = [_result("a", "passed")]
            events = process_results(results, sf)

            assert len(events) == 1
            assert events[0] == ("accepted", "a", "burning_in", "stable")
            assert sf.get_test_state("a") == "stable"

    def test_burning_in_rejected(self):
        """Burning-in test with many failures is rejected as flaky."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "burning_in", runs=19, passes=0)
            for _ in range(19):
                sf.record_run("a", False)
            sf.save()

            results = [_result("a", "failed")]
            events = process_results(results, sf)

            assert len(events) == 1
            assert events[0] == ("rejected", "a", "burning_in", "flaky")
            assert sf.get_test_state("a") == "flaky"

    def test_burning_in_continue(self):
        """Burning-in test with few runs stays in burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "burning_in", runs=2, passes=2)
            sf.save()

            results = [_result("a", "passed")]
            events = process_results(results, sf)

            assert events == []
            assert sf.get_test_state("a") == "burning_in"


class TestProcessResultsDemotion:
    """Tests for process_results handling stable test demotion."""

    def test_stable_failure_demotes_with_history(self):
        """Stable test with enough failure history is demoted to flaky."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "stable", runs=55, passes=50)
            # Pre-populate with recent failures (newest-first)
            for _ in range(5):
                sf.record_run("a", passed=False, commit="prev")
            for _ in range(50):
                sf.record_run("a", passed=True, commit="older")
            sf.save()

            results = [_result("a", "failed")]
            events = process_results(results, sf)

            assert len(events) == 1
            assert events[0] == ("demoted", "a", "stable", "flaky")
            assert sf.get_test_state("a") == "flaky"

    def test_stable_failure_retains_with_low_threshold(self):
        """Stable test retains when observed reliability meets a low threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            # With min_reliability=0.50, a test with mostly passes retains
            # even after a failure because observed rate stays above 50%.
            sf.set_config(min_reliability=0.50, statistical_significance=0.95)
            sf.set_test_state("a", "stable", runs=50, passes=50)
            for _ in range(50):
                sf.record_run("a", passed=True)
            sf.save()

            results = [_result("a", "failed")]
            events = process_results(results, sf)

            # SPRT should retain: observed ~49/50 = 98% >> 50% threshold
            assert events == []
            assert sf.get_test_state("a") == "stable"

    def test_stable_failure_inconclusive_to_burning_in(self):
        """Stable test with inconclusive SPRT moves to burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "stable", runs=3, passes=2)
            # Very little history — SPRT will be inconclusive
            sf.record_run("a", passed=True)
            sf.record_run("a", passed=True)
            sf.save()

            results = [_result("a", "failed")]
            events = process_results(results, sf)

            assert len(events) == 1
            assert events[0] == ("suspicious", "a", "stable", "burning_in")
            assert sf.get_test_state("a") == "burning_in"

    def test_default_stable_failure_not_evaluated(self):
        """Test not in status file (default stable) is not evaluated for demotion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            # "a" is NOT in the status file
            results = [_result("a", "failed")]
            events = process_results(results, sf)

            # No demotion evaluation for unknown tests
            assert events == []
            assert sf.get_test_state("a") == "new"

    def test_stable_pass_no_evaluation(self):
        """Passing stable test records result without evaluation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "stable", runs=50, passes=50)
            sf.save()
            results = [_result("a", "passed")]
            events = process_results(results, sf)

            assert events == []
            assert sf.get_test_state("a") == "stable"
            assert sf.get_test_entry("a")["runs"] == 51


class TestProcessResultsDisabled:
    """Tests for process_results skipping disabled tests."""

    def test_disabled_test_skipped(self):
        """Disabled test result is not recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("a", "disabled", runs=0, passes=0)
            sf.save()

            results = [_result("a", "passed")]
            events = process_results(results, sf)

            assert events == []
            # Runs should NOT be incremented
            entry = sf.get_test_entry("a")
            assert entry is not None
            assert entry["runs"] == 0


class TestSyncDisabledState:
    """Tests for sync_disabled_state()."""

    def test_sync_disables_test(self):
        """Test marked disabled in DAG transitions to disabled state."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            manifest["test_set_tests"]["a"]["disabled"] = True
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable", runs=50, passes=50)
                sf.save()

                events = sync_disabled_state(dag, sf)
                assert len(events) == 1
                assert events[0] == ("disabled", "a", "stable", "disabled")
                assert sf.get_test_state("a") == "disabled"
        finally:
            os.unlink(pass_exe)

    def test_sync_re_enables_test(self):
        """Test no longer disabled in DAG transitions from disabled to new."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "disabled", runs=0, passes=0)
                sf.save()

                events = sync_disabled_state(dag, sf)
                assert len(events) == 1
                assert events[0] == ("re-enabled", "a", "disabled", "new")
                assert sf.get_test_state("a") == "new"
        finally:
            os.unlink(pass_exe)

    def test_sync_idempotent_already_disabled(self):
        """Already disabled test stays disabled without generating events."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            manifest["test_set_tests"]["a"]["disabled"] = True
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "disabled", runs=0, passes=0)
                sf.save()

                events = sync_disabled_state(dag, sf)
                assert events == []
        finally:
            os.unlink(pass_exe)

    def test_sync_no_change_for_active_test(self):
        """Non-disabled test in active state generates no events."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable", runs=50, passes=50)
                sf.save()

                events = sync_disabled_state(dag, sf)
                assert events == []
                assert sf.get_test_state("a") == "stable"
        finally:
            os.unlink(pass_exe)

    def test_sync_new_disabled_test(self):
        """Newly added disabled test (not in status file) gets disabled state."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
            })
            manifest["test_set_tests"]["a"]["disabled"] = True
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.save()

                events = sync_disabled_state(dag, sf)
                assert len(events) == 1
                assert events[0] == ("disabled", "a", "new", "disabled")
                assert sf.get_test_state("a") == "disabled"
        finally:
            os.unlink(pass_exe)


class TestFilterDisabled:
    """Tests for filter_tests_by_state excluding disabled tests."""

    def test_disabled_excluded_from_stable_filter(self):
        """Disabled tests are excluded from default stable filter."""
        pass_exe = _make_pass_script()
        try:
            manifest = _make_manifest({
                "a": {"executable": pass_exe, "depends_on": []},
                "b": {"executable": pass_exe, "depends_on": []},
            })
            dag = TestDAG.from_manifest(manifest)

            with tempfile.TemporaryDirectory() as tmpdir:
                sf = StatusFile(Path(tmpdir) / "status.json")
                sf.set_test_state("a", "stable")
                sf.set_test_state("b", "disabled")
                sf.save()

                result = filter_tests_by_state(dag, sf)
                assert result == ["a"]
        finally:
            os.unlink(pass_exe)
