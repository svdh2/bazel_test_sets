"""Unit tests for the burn-in module."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from orchestrator.lifecycle.burnin import BurnInSweep, filter_tests_by_state, handle_stable_failure
from orchestrator.execution.dag import TestDAG
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

                result = handle_stable_failure("a", dag, sf, max_reruns=20)
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
