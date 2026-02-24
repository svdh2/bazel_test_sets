"""Integration tests for ci_gate Starlark rule runner script generation.

Verifies that ci_gate targets generate runner scripts with the correct
baked-in CLI flags based on the rule attributes. Only non-default attributes
should appear as flags; default attributes should be omitted.

These tests require Bazel runfiles (they read generated runner scripts) and
are skipped when run outside Bazel (e.g., via standalone pytest).
"""

from __future__ import annotations

import os
import unittest

# Skip all tests in this module when not running inside Bazel
_INSIDE_BAZEL = bool(
    os.environ.get("TEST_SRCDIR")
    or os.environ.get("RUNFILES_DIR")
)
_SKIP_REASON = "Requires Bazel runfiles (run via 'bazel test', not standalone pytest)"


def _find_runfiles_dir() -> str:
    """Locate the Bazel runfiles directory."""
    # Check RUNFILES_DIR environment variable first
    runfiles = os.environ.get("RUNFILES_DIR")
    if runfiles and os.path.isdir(runfiles):
        return runfiles

    # Try TEST_SRCDIR (set by Bazel for test targets)
    test_srcdir = os.environ.get("TEST_SRCDIR")
    if test_srcdir and os.path.isdir(test_srcdir):
        return test_srcdir

    raise RuntimeError("Cannot find Bazel runfiles directory")


def _read_runner_script(name: str) -> str:
    """Read a ci_gate runner script from runfiles."""
    runfiles = _find_runfiles_dir()
    path = os.path.join(runfiles, "_main", "tests", f"{name}_runner.sh")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Runner script not found: {path}")
    with open(path) as f:
        return f.read()


@unittest.skipUnless(_INSIDE_BAZEL, _SKIP_REASON)
class TestLocalGateRunner(unittest.TestCase):
    """Tests for the local_gate ci_gate target (all defaults)."""

    def setUp(self) -> None:
        self.script = _read_runner_script("local_gate_test")

    def test_shebang(self) -> None:
        assert "#!/bin/bash" in self.script

    def test_header_comment(self) -> None:
        assert "ci_gate runner for: local_gate_test" in self.script

    def test_test_set_comment(self) -> None:
        assert "test_set: sample_tests" in self.script

    def test_manifest_flag(self) -> None:
        assert "--manifest" in self.script

    def test_output_flag(self) -> None:
        assert "--output" in self.script

    def test_discover_workspace_tests_flag(self) -> None:
        assert "--discover-workspace-tests" in self.script

    def test_mode_diagnostic(self) -> None:
        assert '--mode "diagnostic"' in self.script

    def test_passthrough(self) -> None:
        assert '"$@"' in self.script

    def test_no_effort_flag(self) -> None:
        assert "--effort" not in self.script

    def test_no_max_reruns_flag(self) -> None:
        assert "--max-reruns" not in self.script

    def test_no_max_failures_flag(self) -> None:
        assert "--max-failures" not in self.script

    def test_no_max_parallel_flag(self) -> None:
        assert "--max-parallel" not in self.script

    def test_no_status_file_flag(self) -> None:
        assert "--status-file" not in self.script

    def test_no_diff_base_flag(self) -> None:
        assert "--diff-base" not in self.script

    def test_no_co_occurrence_graph_flag(self) -> None:
        assert "--co-occurrence-graph" not in self.script

    def test_no_max_test_percentage_flag(self) -> None:
        assert "--max-test-percentage" not in self.script

    def test_no_max_hops_flag(self) -> None:
        assert "--max-hops" not in self.script

    def test_no_min_reliability_flag(self) -> None:
        assert "--min-reliability" not in self.script

    def test_no_statistical_significance_flag(self) -> None:
        assert "--statistical-significance" not in self.script

    def test_no_skip_unchanged_flag(self) -> None:
        assert "--no-skip-unchanged" not in self.script

    def test_no_flaky_deadline_days_flag(self) -> None:
        assert "--flaky-deadline-days" not in self.script


@unittest.skipUnless(_INSIDE_BAZEL, _SKIP_REASON)
class TestPrGateRunner(unittest.TestCase):
    """Tests for the pr_gate ci_gate target (regression effort)."""

    def setUp(self) -> None:
        self.script = _read_runner_script("pr_gate_test")

    def test_mode_diagnostic(self) -> None:
        assert '--mode "diagnostic"' in self.script

    def test_effort_regression(self) -> None:
        assert '--effort "regression"' in self.script

    def test_max_reruns_5(self) -> None:
        assert "--max-reruns 5" in self.script

    def test_diff_base_main(self) -> None:
        assert '--diff-base "main"' in self.script

    def test_passthrough(self) -> None:
        assert '"$@"' in self.script

    def test_uses_sample_tests_manifest(self) -> None:
        assert "sample_tests_manifest.json" in self.script

    def test_no_status_file(self) -> None:
        assert "--status-file" not in self.script

    def test_no_max_failures(self) -> None:
        assert "--max-failures" not in self.script

    def test_no_max_parallel(self) -> None:
        assert "--max-parallel" not in self.script

    def test_no_skip_unchanged_flag(self) -> None:
        assert "--no-skip-unchanged" not in self.script

    def test_no_min_reliability(self) -> None:
        assert "--min-reliability" not in self.script


@unittest.skipUnless(_INSIDE_BAZEL, _SKIP_REASON)
class TestMergeGateRunner(unittest.TestCase):
    """Tests for the merge_gate ci_gate target (converge with non-defaults)."""

    def setUp(self) -> None:
        self.script = _read_runner_script("merge_gate_test")

    def test_mode_detection(self) -> None:
        assert '--mode "detection"' in self.script

    def test_effort_converge(self) -> None:
        assert '--effort "converge"' in self.script

    def test_max_reruns_10(self) -> None:
        assert "--max-reruns 10" in self.script

    def test_max_failures_1(self) -> None:
        assert "--max-failures 1" in self.script

    def test_min_reliability_0999(self) -> None:
        assert "--min-reliability 0.999" in self.script

    def test_no_skip_unchanged(self) -> None:
        assert "--no-skip-unchanged" in self.script

    def test_passthrough(self) -> None:
        assert '"$@"' in self.script

    def test_uses_basic_tests_manifest(self) -> None:
        assert "basic_tests_manifest.json" in self.script

    def test_test_set_comment(self) -> None:
        assert "test_set: basic_tests" in self.script

    def test_no_status_file(self) -> None:
        assert "--status-file" not in self.script

    def test_no_diff_base(self) -> None:
        assert "--diff-base" not in self.script

    def test_no_co_occurrence_graph(self) -> None:
        assert "--co-occurrence-graph" not in self.script

    def test_no_max_hops(self) -> None:
        assert "--max-hops" not in self.script

    def test_no_flaky_deadline_days(self) -> None:
        assert "--flaky-deadline-days" not in self.script


if __name__ == "__main__":
    unittest.main()
