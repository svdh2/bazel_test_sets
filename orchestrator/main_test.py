"""Tests for the orchestrator main entry point."""

from __future__ import annotations

import datetime
import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.analysis.measurements import store_measurements
from orchestrator.execution.dag import TestDAG
from orchestrator.lifecycle.status import StatusFile
from orchestrator.main import (
    _compute_and_filter_hashes,
    _filter_manifest,
    _get_changed_files,
    _resolve_git_context,
    cmd_build_graph,
    cmd_burn_in,
    cmd_deflake,
    cmd_re_judge,
    cmd_test_status,
    parse_args,
)


def _make_args(**kwargs):
    """Create a simple namespace for testing."""
    import argparse
    return argparse.Namespace(**kwargs)


class TestParseArgsEffort:
    """Tests for effort option argument parsing."""

    def test_effort_regression_accepted(self):
        """--effort regression is a valid flag combinable with any mode."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--mode", "diagnostic",
            "--effort", "regression",
            "--diff-base", "main",
        ])
        assert args.mode == "diagnostic"
        assert args.effort == "regression"
        assert args.diff_base == "main"

    def test_effort_regression_with_detection_mode(self):
        """--effort regression combines with --mode=detection."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--mode", "detection",
            "--effort", "regression",
            "--diff-base", "main",
        ])
        assert args.mode == "detection"
        assert args.effort == "regression"

    def test_effort_default_none(self):
        """--effort defaults to None."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.effort is None

    def test_effort_converge_accepted(self):
        """--effort converge is a valid choice."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--effort", "converge",
        ])
        assert args.effort == "converge"

    def test_effort_max_accepted(self):
        """--effort max is a valid choice."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--effort", "max",
        ])
        assert args.effort == "max"

    def test_effort_invalid_choice_rejected(self):
        """--effort with invalid choice raises SystemExit."""
        with pytest.raises(SystemExit):
            parse_args([
                "--manifest", "/path/manifest.json",
                "--effort", "invalid",
            ])

    def test_diff_base_flag(self):
        """--diff-base flag parsed correctly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--diff-base", "HEAD~3",
        ])
        assert args.diff_base == "HEAD~3"

    def test_changed_files_flag(self):
        """--changed-files flag parsed correctly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--changed-files", "src/a.py,src/b.py",
        ])
        assert args.changed_files == "src/a.py,src/b.py"

    def test_co_occurrence_graph_flag(self):
        """--co-occurrence-graph flag parsed correctly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--co-occurrence-graph", "/custom/graph.json",
        ])
        assert args.co_occurrence_graph == Path("/custom/graph.json")

    def test_default_values(self):
        """Default values for effort-related flags."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.diff_base is None
        assert args.changed_files is None
        assert args.effort is None
        assert args.co_occurrence_graph == Path(".tests/co_occurrence_graph.json")
        assert args.allow_dirty is False

    def test_allow_dirty_flag(self):
        """--allow-dirty flag parsed correctly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--allow-dirty",
        ])
        assert args.allow_dirty is True


class TestParseArgsCiGateFlags:
    """Tests for ci_gate parameter CLI flags."""

    def test_max_reruns_default(self):
        """--max-reruns defaults to 100."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.max_reruns == 100

    def test_max_reruns_override(self):
        """--max-reruns accepts an integer value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-reruns", "5",
        ])
        assert args.max_reruns == 5

    def test_max_failures_default(self):
        """--max-failures defaults to None."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.max_failures is None

    def test_max_failures_override(self):
        """--max-failures accepts an integer value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-failures", "3",
        ])
        assert args.max_failures == 3

    def test_max_parallel_default(self):
        """--max-parallel defaults to None."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.max_parallel is None

    def test_max_parallel_override(self):
        """--max-parallel accepts an integer value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-parallel", "8",
        ])
        assert args.max_parallel == 8

    def test_status_file_default(self):
        """--status-file defaults to None."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.status_file is None

    def test_status_file_override(self):
        """--status-file accepts a path value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--status-file", ".tests/status",
        ])
        assert args.status_file == Path(".tests/status")

    def test_max_test_percentage_default(self):
        """--max-test-percentage defaults to 0.10."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.max_test_percentage == 0.10

    def test_max_test_percentage_override(self):
        """--max-test-percentage accepts a float value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-test-percentage", "0.25",
        ])
        assert args.max_test_percentage == 0.25

    def test_max_hops_default(self):
        """--max-hops defaults to 2."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.max_hops == 2

    def test_max_hops_override(self):
        """--max-hops accepts an integer value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-hops", "3",
        ])
        assert args.max_hops == 3

    def test_skip_unchanged_default_true(self):
        """--skip-unchanged defaults to True."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.skip_unchanged is True

    def test_skip_unchanged_explicit(self):
        """--skip-unchanged can be set explicitly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--skip-unchanged",
        ])
        assert args.skip_unchanged is True

    def test_no_skip_unchanged(self):
        """--no-skip-unchanged sets skip_unchanged to False."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--no-skip-unchanged",
        ])
        assert args.skip_unchanged is False

    def test_min_reliability_default(self):
        """--min-reliability defaults to 0.99."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.min_reliability == 0.99

    def test_min_reliability_override(self):
        """--min-reliability accepts a float value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--min-reliability", "0.999",
        ])
        assert args.min_reliability == 0.999

    def test_statistical_significance_default(self):
        """--statistical-significance defaults to 0.95."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.statistical_significance == 0.95

    def test_statistical_significance_override(self):
        """--statistical-significance accepts a float value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--statistical-significance", "0.90",
        ])
        assert args.statistical_significance == 0.90

    def test_flaky_deadline_days_default(self):
        """--flaky-deadline-days defaults to 14."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.flaky_deadline_days == 14

    def test_flaky_deadline_days_override(self):
        """--flaky-deadline-days accepts an integer value."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--flaky-deadline-days", "7",
        ])
        assert args.flaky_deadline_days == 7

    def test_all_defaults_match_default_config(self):
        """Default CLI flag values match DEFAULT_CONFIG values."""
        from orchestrator.lifecycle.config import DEFAULT_CONFIG

        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.max_reruns == DEFAULT_CONFIG["max_reruns"]
        assert args.max_failures == DEFAULT_CONFIG["max_failures"]
        assert args.max_parallel == DEFAULT_CONFIG["max_parallel"]
        assert args.status_file is None  # DEFAULT_CONFIG["status_file"] is None
        assert args.max_test_percentage == DEFAULT_CONFIG["max_test_percentage"]
        assert args.max_hops == DEFAULT_CONFIG["max_hops"]
        assert args.min_reliability == DEFAULT_CONFIG["min_reliability"]
        assert args.statistical_significance == DEFAULT_CONFIG["statistical_significance"]

    def test_all_flags_combined(self):
        """All ci_gate flags can be combined."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-reruns", "5",
            "--max-failures", "3",
            "--max-parallel", "4",
            "--status-file", ".tests/status",
            "--max-test-percentage", "0.20",
            "--max-hops", "3",
            "--no-skip-unchanged",
            "--min-reliability", "0.999",
            "--statistical-significance", "0.90",
            "--flaky-deadline-days", "7",
        ])
        assert args.max_reruns == 5
        assert args.max_failures == 3
        assert args.max_parallel == 4
        assert args.status_file == Path(".tests/status")
        assert args.max_test_percentage == 0.20
        assert args.max_hops == 3
        assert args.skip_unchanged is False
        assert args.min_reliability == 0.999
        assert args.statistical_significance == 0.90
        assert args.flaky_deadline_days == 7


class TestParseArgsSubcommands:
    """Tests for lifecycle subcommand argument parsing."""

    def test_burn_in_command(self):
        """Parse burn-in subcommand."""
        args = parse_args(["burn-in", "//test:a", "//test:b"])
        assert args.command == "burn-in"
        assert args.tests == ["//test:a", "//test:b"]

    def test_burn_in_no_tests(self):
        """Parse burn-in without tests."""
        args = parse_args(["burn-in"])
        assert args.command == "burn-in"
        assert args.tests == []

    def test_deflake_command(self):
        """Parse deflake subcommand."""
        args = parse_args(["deflake", "//test:a"])
        assert args.command == "deflake"
        assert args.tests == ["//test:a"]

    def test_test_status_command(self):
        """Parse test-status subcommand."""
        args = parse_args(["test-status"])
        assert args.command == "test-status"

    def test_test_status_with_filter(self):
        """Parse test-status with state filter."""
        args = parse_args(["test-status", "--state", "stable"])
        assert args.command == "test-status"
        assert args.state == "stable"

    def test_test_status_disabled_filter(self):
        """Parse test-status with disabled state filter."""
        args = parse_args(["test-status", "--state", "disabled"])
        assert args.command == "test-status"
        assert args.state == "disabled"

    def test_custom_status_file(self):
        """Parse custom status file path."""
        args = parse_args(["test-status", "--status-file", "/custom/path"])
        assert args.status_file == Path("/custom/path")

    def test_re_judge_command(self):
        """Parse re-judge subcommand."""
        args = parse_args([
            "re-judge", "--manifest", "/path/manifest.json", "//test:a",
        ])
        assert args.command == "re-judge"
        assert args.manifest == Path("/path/manifest.json")
        assert args.tests == ["//test:a"]

    def test_re_judge_no_tests(self):
        """Parse re-judge without tests (re-judges all eligible)."""
        args = parse_args([
            "re-judge", "--manifest", "/path/manifest.json",
        ])
        assert args.command == "re-judge"
        assert args.tests == []

    def test_re_judge_custom_measurements_dir(self):
        """Parse re-judge with custom measurements directory."""
        args = parse_args([
            "re-judge", "--manifest", "/path/manifest.json",
            "--measurements-dir", "/custom/measurements",
        ])
        assert args.measurements_dir == Path("/custom/measurements")

    def test_build_graph_command(self):
        """Parse build-graph subcommand."""
        args = parse_args(["build-graph"])
        assert args.command == "build-graph"

    def test_build_graph_custom_output(self):
        """Parse build-graph with custom output path."""
        args = parse_args([
            "build-graph", "--output", "/custom/graph.json",
        ])
        assert args.output == Path("/custom/graph.json")

    def test_build_graph_with_options(self):
        """Parse build-graph with all options."""
        args = parse_args([
            "build-graph",
            "--source-extensions", ".py,.java",
            "--test-patterns", "*_test.*,test_*.*",
            "--max-history", "5000",
            "--repo-root", "/path/to/repo",
        ])
        assert args.source_extensions == ".py,.java"
        assert args.test_patterns == "*_test.*,test_*.*"
        assert args.max_history == 5000
        assert args.repo_root == Path("/path/to/repo")


class TestFilterManifest:
    """Tests for manifest filtering."""

    def test_filter_selects_only_named_tests(self):
        """Filter includes only selected tests."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "a": {"assertion": "a", "executable": "a", "depends_on": []},
                "b": {"assertion": "b", "executable": "b", "depends_on": ["a"]},
                "c": {"assertion": "c", "executable": "c", "depends_on": []},
            },
        }
        filtered = _filter_manifest(manifest, ["a", "b"])
        assert "a" in filtered["test_set_tests"]
        assert "b" in filtered["test_set_tests"]
        assert "c" not in filtered["test_set_tests"]

    def test_filter_preserves_internal_deps(self):
        """Filter preserves depends_on for included tests."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "a": {"assertion": "a", "executable": "a", "depends_on": []},
                "b": {"assertion": "b", "executable": "b", "depends_on": ["a"]},
            },
        }
        filtered = _filter_manifest(manifest, ["a", "b"])
        assert filtered["test_set_tests"]["b"]["depends_on"] == ["a"]

    def test_filter_removes_external_deps(self):
        """Filter removes depends_on for excluded tests."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "a": {"assertion": "a", "executable": "a", "depends_on": []},
                "b": {"assertion": "b", "executable": "b", "depends_on": ["a", "c"]},
                "c": {"assertion": "c", "executable": "c", "depends_on": []},
            },
        }
        # Include b but not c
        filtered = _filter_manifest(manifest, ["a", "b"])
        assert filtered["test_set_tests"]["b"]["depends_on"] == ["a"]

    def test_filter_empty_selection(self):
        """Empty selection produces empty manifest."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "a": {"assertion": "a", "executable": "a", "depends_on": []},
            },
        }
        filtered = _filter_manifest(manifest, [])
        assert filtered["test_set_tests"] == {}


class TestBurnIn:
    """Tests for burn-in subcommand."""

    def test_burn_in_new_to_burning_in(self):
        """Burn-in transitions new test to burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "new")
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "burning_in"
            assert len(sf2.get_test_history("//test:a")) == 0

    def test_burn_in_unknown_test(self):
        """Burn-in of test not in status file creates it as burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=["//test:new"],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:new") == "burning_in"

    def test_burn_in_already_burning_in(self):
        """Burn-in of already burning_in test is a no-op."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "burning_in")
            for _ in range(5):
                sf.record_run("//test:a", passed=True)
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert len(sf2.get_test_history("//test:a")) == 5  # Unchanged

    def test_burn_in_stable_rejected(self):
        """Burn-in of stable test is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "stable")
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0  # Not an error, just warns

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "stable"

    def test_burn_in_disabled_rejected(self):
        """Burn-in of disabled test is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "disabled", clear_history=True)
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0  # Not an error, just warns

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "disabled"

    def test_burn_in_list_all(self):
        """Burn-in with no tests lists all burning_in tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "burning_in")
            for _ in range(5):
                sf.record_run("//test:a", passed=True)
            sf.set_test_state("//test:b", "stable")
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=[],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0


class TestDeflake:
    """Tests for deflake subcommand."""

    def test_deflake_flaky_to_burning_in(self):
        """Deflake transitions flaky test to burning_in with reset counters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "flaky")
            sf.save()

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "burning_in"
            assert len(sf2.get_test_history("//test:a")) == 0

    def test_deflake_not_found(self):
        """Deflake of nonexistent test returns error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.save()

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:nonexistent"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 1

    def test_deflake_stable_rejected(self):
        """Deflake of stable test is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "stable")
            sf.save()

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 1

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "stable"

    def test_deflake_disabled_rejected(self):
        """Deflake of disabled test is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "disabled", clear_history=True)
            sf.save()

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 1

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "disabled"

    def test_deflake_multiple(self):
        """Deflake multiple tests at once."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "flaky")
            sf.set_test_state("//test:b", "flaky")
            sf.save()

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:a", "//test:b"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "burning_in"
            assert sf2.get_test_state("//test:b") == "burning_in"

    def test_deflake_clears_target_hash(self):
        """Deflake clears target_hash so test gets fresh hash tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "flaky")
            sf.set_target_hash("//test:a", "old_hash_abc")
            sf.record_run("//test:a", True, target_hash="old_hash_abc")
            sf.save()

            # Verify hash is set before deflake
            assert sf.get_target_hash("//test:a") == "old_hash_abc"

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "burning_in"
            assert len(sf2.get_test_history("//test:a")) == 0
            assert sf2.get_target_hash("//test:a") is None

    def test_deflake_without_target_hash(self):
        """Deflake works fine when no target_hash was set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "flaky")
            sf.save()

            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("//test:a") == "burning_in"
            assert sf2.get_target_hash("//test:a") is None

    def test_deflake_workflow_to_stable(self):
        """Full workflow: flaky -> deflake -> burning_in -> evidence -> stable."""
        from orchestrator.lifecycle.burnin import process_results
        from orchestrator.execution.executor import TestResult

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"

            # Step 1: Test starts as flaky with old hash
            sf = StatusFile(status_path, min_reliability=0.99,
                            statistical_significance=0.95)
            sf.set_test_state("//test:fixed", "flaky")
            sf.set_target_hash("//test:fixed", "old_hash")
            sf.save()

            # Step 2: Deflake the test
            args = _make_args(
                command="deflake",
                status_file=status_path,
                tests=["//test:fixed"],
            )
            exit_code = cmd_deflake(args)
            assert exit_code == 0

            # Verify deflake results
            sf = StatusFile(status_path, min_reliability=0.99,
                            statistical_significance=0.95)
            assert sf.get_test_state("//test:fixed") == "burning_in"
            assert sf.get_target_hash("//test:fixed") is None
            assert len(sf.get_test_history("//test:fixed")) == 0

            # Step 3: Simulate CI runs recording evidence (30 passes)
            for _ in range(30):
                result = TestResult(
                    name="//test:fixed",
                    assertion="test works",
                    status="passed",
                    duration=0.1,
                    stdout="",
                    stderr="",
                    exit_code=0,
                )
                events = process_results(
                    [result], sf, commit_sha="new_commit",
                    target_hashes={"//test:fixed": "new_hash"},
                )

            # After 30 consecutive passes, SPRT should accept -> stable
            assert sf.get_test_state("//test:fixed") == "stable"

    def test_disabled_to_new_repromotion_via_sync(self):
        """Disabled -> new re-promotion via sync_disabled_state when BUILD removes disabled flag."""
        from orchestrator.lifecycle.burnin import sync_disabled_state
        from orchestrator.execution.dag import TestDAG

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a non-disabled test in manifest
            script_path = Path(tmpdir) / "test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "//test:was_disabled": {
                        "assertion": "check",
                        "executable": str(script_path),
                        "depends_on": [],
                        # NOT disabled in manifest
                    },
                },
            }
            dag = TestDAG.from_manifest(manifest)

            # Status file has test as disabled
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:was_disabled", "disabled")
            sf.save()

            # Sync should transition disabled -> new
            events = sync_disabled_state(dag, sf)

            assert len(events) == 1
            assert events[0] == ("re-enabled", "//test:was_disabled", "disabled", "new")
            assert sf.get_test_state("//test:was_disabled") == "new"


class TestTestStatus:
    """Tests for test-status subcommand."""

    def test_test_status_empty(self):
        """Status with no tests shows empty message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.save()

            args = _make_args(
                command="test-status",
                status_file=status_path,
                state=None,
            )
            exit_code = cmd_test_status(args)
            assert exit_code == 0

    def test_test_status_displays_all(self):
        """Status displays all tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "burning_in")
            sf.set_test_state("//test:c", "flaky")
            sf.save()

            args = _make_args(
                command="test-status",
                status_file=status_path,
                state=None,
            )
            exit_code = cmd_test_status(args)
            assert exit_code == 0

    def test_test_status_filter_by_state(self):
        """Status filters by state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "flaky")
            sf.save()

            args = _make_args(
                command="test-status",
                status_file=status_path,
                state="flaky",
            )
            exit_code = cmd_test_status(args)
            assert exit_code == 0


class TestMainEntryPoint:
    """Tests for the main() entry point."""

    def test_no_manifest_no_subcommand(self):
        """No manifest and no subcommand returns 1."""
        from orchestrator.main import main

        exit_code = main([])
        assert exit_code == 1

    def test_burn_in_via_main(self):
        """burn-in subcommand accessible via main."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.save()

            exit_code = main([
                "burn-in",
                "--status-file", str(status_path),
                "//test:a",
            ])
            assert exit_code == 0

    def test_test_status_via_main(self):
        """test-status subcommand accessible via main."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.save()

            exit_code = main([
                "test-status",
                "--status-file", str(status_path),
            ])
            assert exit_code == 0

    def test_re_judge_via_main(self):
        """re-judge subcommand accessible via main."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create manifest with no judgement targets
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {},
            }))

            exit_code = main([
                "re-judge",
                "--manifest", str(manifest_path),
                "--measurements-dir", str(Path(tmpdir) / "measurements"),
            ])
            assert exit_code == 0


def _make_judge_script(tmpdir: str, name: str, exit_code: int = 0) -> str:
    """Create a simple judgement script."""
    script_path = Path(tmpdir) / name
    script_path.write_text(
        f"#!/bin/bash\nexit {exit_code}\n"
    )
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return str(script_path)


class TestReJudge:
    """Tests for re-judge subcommand."""

    def test_re_judge_pass(self):
        """Re-judge with passing judgement target."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = _make_judge_script(tmpdir, "judge.sh", exit_code=0)
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements(
                "test_a", [{"name": "x", "value": 1}], measurements_dir
            )

            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "test_a": {
                        "assertion": "test",
                        "executable": "/bin/test",
                        "depends_on": [],
                        "judgement_executable": script,
                    },
                },
            }))

            args = _make_args(
                command="re-judge",
                manifest=manifest_path,
                measurements_dir=measurements_dir,
                tests=["test_a"],
            )
            exit_code = cmd_re_judge(args)
            assert exit_code == 0

    def test_re_judge_fail(self):
        """Re-judge with failing judgement target."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = _make_judge_script(tmpdir, "judge.sh", exit_code=1)
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements(
                "test_a", [{"name": "x", "value": 1}], measurements_dir
            )

            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "test_a": {
                        "assertion": "test",
                        "executable": "/bin/test",
                        "depends_on": [],
                        "judgement_executable": script,
                    },
                },
            }))

            args = _make_args(
                command="re-judge",
                manifest=manifest_path,
                measurements_dir=measurements_dir,
                tests=["test_a"],
            )
            exit_code = cmd_re_judge(args)
            assert exit_code == 1

    def test_re_judge_no_eligible(self):
        """Re-judge with no eligible tests returns 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "test_a": {
                        "assertion": "test",
                        "executable": "/bin/test",
                        "depends_on": [],
                    },
                },
            }))

            args = _make_args(
                command="re-judge",
                manifest=manifest_path,
                measurements_dir=Path(tmpdir) / "measurements",
                tests=[],
            )
            exit_code = cmd_re_judge(args)
            assert exit_code == 0

    def test_re_judge_missing_manifest(self):
        """Re-judge with missing manifest returns 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            args = _make_args(
                command="re-judge",
                manifest=Path(tmpdir) / "nonexistent.json",
                measurements_dir=Path(tmpdir) / "measurements",
                tests=["test_a"],
            )
            exit_code = cmd_re_judge(args)
            assert exit_code == 1

    def test_re_judge_skip_no_measurements(self):
        """Re-judge skips test with no stored measurements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = _make_judge_script(tmpdir, "judge.sh")
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "test_a": {
                        "assertion": "test",
                        "executable": "/bin/test",
                        "depends_on": [],
                        "judgement_executable": script,
                    },
                },
            }))

            measurements_dir = Path(tmpdir) / "measurements"
            measurements_dir.mkdir()

            args = _make_args(
                command="re-judge",
                manifest=manifest_path,
                measurements_dir=measurements_dir,
                tests=["test_a"],
            )
            exit_code = cmd_re_judge(args)
            # Skipped tests don't count as failures
            assert exit_code == 0


class TestEffortRegressionMissingGraph:
    """Tests for effort regression with missing co-occurrence graph."""

    def test_regression_missing_graph(self):
        """Missing graph file produces clear error."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests"},
                "test_set_tests": {},
            }))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--effort", "regression",
                "--changed-files", "src/a.py",
                "--co-occurrence-graph", str(Path(tmpdir) / "nonexistent.json"),
            ])
            assert exit_code == 1

    def test_regression_no_diff_base_or_changed_files(self):
        """--effort regression without --diff-base or --changed-files errors."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests"},
                "test_set_tests": {},
            }))

            # Create a graph file
            graph_path = Path(tmpdir) / "graph.json"
            graph_path.write_text(json.dumps({
                "metadata": {},
                "file_commits": {},
                "commit_files": {},
            }))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--effort", "regression",
                "--co-occurrence-graph", str(graph_path),
            ])
            assert exit_code == 1


class TestEffortRegressionEndToEnd:
    """End-to-end tests for effort regression."""

    def test_regression_diagnostic_with_changed_files(self):
        """--effort regression with diagnostic mode runs selected tests."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple pass-script
            script_path = Path(tmpdir) / "pass_test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            # Create manifest with one test
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "auth_test": {
                        "assertion": "Auth works",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }))

            # Create co-occurrence graph
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            graph = {
                "metadata": {
                    "last_commit": "abc",
                    "total_commits_analyzed": 1,
                    "source_extensions": [".py"],
                    "test_patterns": ["*_test.*"],
                },
                "file_commits": {
                    "src/auth.py": [
                        {"commit": "c1", "timestamp": ts},
                    ],
                },
                "commit_files": {
                    "c1": {
                        "timestamp": ts,
                        "source_files": ["src/auth.py"],
                        "test_files": ["tests/auth_test.py"],
                    },
                },
            }
            graph_path = Path(tmpdir) / "graph.json"
            graph_path.write_text(json.dumps(graph))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--mode", "diagnostic",
                "--effort", "regression",
                "--changed-files", "src/auth.py",
                "--co-occurrence-graph", str(graph_path),
                "--max-parallel", "1",
            ])
            assert exit_code == 0

    def test_regression_detection_with_changed_files(self):
        """--effort regression with detection mode runs selected tests."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple pass-script
            script_path = Path(tmpdir) / "pass_test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            # Create manifest with one test
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "auth_test": {
                        "assertion": "Auth works",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }))

            # Create co-occurrence graph
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).isoformat()
            graph = {
                "metadata": {
                    "last_commit": "abc",
                    "total_commits_analyzed": 1,
                    "source_extensions": [".py"],
                    "test_patterns": ["*_test.*"],
                },
                "file_commits": {
                    "src/auth.py": [
                        {"commit": "c1", "timestamp": ts},
                    ],
                },
                "commit_files": {
                    "c1": {
                        "timestamp": ts,
                        "source_files": ["src/auth.py"],
                        "test_files": ["tests/auth_test.py"],
                    },
                },
            }
            graph_path = Path(tmpdir) / "graph.json"
            graph_path.write_text(json.dumps(graph))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--mode", "detection",
                "--effort", "regression",
                "--changed-files", "src/auth.py",
                "--co-occurrence-graph", str(graph_path),
                "--max-parallel", "1",
            ])
            assert exit_code == 0

    def test_regression_no_changed_files_returns_zero(self):
        """--effort regression with empty changed files returns 0."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests"},
                "test_set_tests": {},
            }))

            graph_path = Path(tmpdir) / "graph.json"
            graph_path.write_text(json.dumps({
                "metadata": {},
                "file_commits": {},
                "commit_files": {},
            }))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--effort", "regression",
                "--changed-files", "",
                "--co-occurrence-graph", str(graph_path),
            ])
            assert exit_code == 0


class TestMiniConvergeRegression:
    """Tests for mini-converge in regression mode with status_file."""

    def _make_graph(self, tmpdir: str, test_name: str) -> Path:
        """Create a co-occurrence graph that selects the given test."""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        graph = {
            "metadata": {
                "last_commit": "abc",
                "total_commits_analyzed": 1,
                "source_extensions": [".py"],
                "test_patterns": ["*_test.*"],
            },
            "file_commits": {
                "src/module.py": [
                    {"commit": "c1", "timestamp": ts},
                ],
            },
            "commit_files": {
                "c1": {
                    "timestamp": ts,
                    "source_files": ["src/module.py"],
                    "test_files": [f"tests/{test_name}.py"],
                },
            },
        }
        graph_path = Path(tmpdir) / "graph.json"
        graph_path.write_text(json.dumps(graph))
        return graph_path

    def test_mini_converge_true_fail_blocks(self):
        """Regression with status_file: consistently failing test -> exit 1."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a failing test script
            script_path = Path(tmpdir) / "fail_test.sh"
            script_path.write_text("#!/bin/bash\nexit 1\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "fail_test": {
                        "assertion": "Should fail",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "fail_test")
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("fail_test", "stable")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch("orchestrator.main._resolve_git_context") as mock_git:
                mock_git.return_value = "abc123"
                exit_code = _run_regression(args, manifest, dag, "abc123")

            # Consistently failing stable test -> blocks (exit 1)
            assert exit_code == 1

    def test_mini_converge_flaky_test_non_blocking(self):
        """Regression with status_file: flaky-state test failure -> exit 0."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a failing test script
            script_path = Path(tmpdir) / "flaky_test.sh"
            script_path.write_text("#!/bin/bash\nexit 1\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "flaky_test": {
                        "assertion": "Flaky check",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "flaky_test")
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("flaky_test", "flaky")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")

            # Flaky test failure is non-blocking in regression mode
            assert exit_code == 0

    def test_mini_converge_burning_in_non_blocking(self):
        """Regression with status_file: burning_in test failure -> exit 0."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "burnin_test.sh"
            script_path.write_text("#!/bin/bash\nexit 1\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "burnin_test")
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("burnin_test", "burning_in")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")

            # burning_in test failure is non-blocking in regression mode
            assert exit_code == 0

    def test_regression_no_status_file_backward_compatible(self):
        """Without status_file, regression uses raw pass/fail (no mini-converge)."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a failing test
            script_path = Path(tmpdir) / "fail_test.sh"
            script_path.write_text("#!/bin/bash\nexit 1\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "fail_test": {
                        "assertion": "Should fail",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "fail_test")

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=None,  # No status file
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, None)

            # No status file -> raw pass/fail, failure blocks
            assert exit_code == 1

    def test_mini_converge_all_pass_exit_zero(self):
        """Regression with status_file: all tests pass -> exit 0 (no mini-converge)."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "pass_test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "pass_test": {
                        "assertion": "Should pass",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "pass_test")
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("pass_test", "stable")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")

            # All pass -> no mini-converge needed, exit 0
            assert exit_code == 0

    def test_mini_converge_records_runs_in_status_file(self):
        """Mini-converge records rerun results in the status file."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "fail_test.sh"
            script_path.write_text("#!/bin/bash\nexit 1\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "fail_test": {
                        "assertion": "Should fail",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "fail_test")
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("fail_test", "stable")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=3,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            _run_regression(args, manifest, dag, "abc123")

            # Reload status file and check history
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("fail_test")
            # Should have initial run + up to max_reruns entries
            assert len(history) >= 2  # At least initial + 1 rerun

    def test_regression_no_commit_sha_no_mini_converge(self):
        """Without commit_sha, regression skips mini-converge."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "fail_test.sh"
            script_path.write_text("#!/bin/bash\nexit 1\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "fail_test": {
                        "assertion": "Should fail",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, "fail_test")
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("fail_test", "stable")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            # No commit_sha -> falls through to raw pass/fail path
            exit_code = _run_regression(args, manifest, dag, None)

            # Without commit SHA, mini-converge doesn't run, raw fail
            assert exit_code == 1


class TestBurnInInclusionRegression:
    """Tests for burn-in test inclusion in regression selection (Step 4.3)."""

    def _make_graph(self, tmpdir: str, test_names: list[str]) -> Path:
        """Create a co-occurrence graph that selects given tests via src/module.py."""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        graph = {
            "metadata": {
                "last_commit": "abc",
                "total_commits_analyzed": 1,
                "source_extensions": [".py"],
                "test_patterns": ["*_test.*"],
            },
            "file_commits": {
                "src/module.py": [
                    {"commit": "c1", "timestamp": ts},
                ],
            },
            "commit_files": {
                "c1": {
                    "timestamp": ts,
                    "source_files": ["src/module.py"],
                    "test_files": [f"tests/{name}.py" for name in test_names],
                },
            },
        }
        graph_path = Path(tmpdir) / "graph.json"
        graph_path.write_text(json.dumps(graph))
        return graph_path

    def _make_script(self, tmpdir: str, name: str, exit_code: int = 0) -> str:
        """Create a test script."""
        script_path = Path(tmpdir) / f"{name}.sh"
        script_path.write_text(f"#!/bin/bash\nexit {exit_code}\n")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return str(script_path)

    def test_burn_in_inclusion_new_tests_added(self):
        """New tests are included in regression selection when status_file is configured."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            # stable_test is selected by co-occurrence
            # new_test is NOT in the co-occurrence graph but IS in manifest + status file
            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "new_test": {
                        "assertion": "New check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Graph only knows about stable_test
            graph_path = self._make_graph(tmpdir, ["stable_test"])

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.set_test_state("new_test", "new")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")

            # Both tests should pass (exit 0)
            assert exit_code == 0

            # Verify new_test was executed by checking status file
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("new_test")
            assert len(history) >= 1, "new_test should have been executed"

    def test_burn_in_inclusion_burning_in_tests_added(self):
        """burning_in tests are included in regression selection."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Graph only selects stable_test
            graph_path = self._make_graph(tmpdir, ["stable_test"])

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.set_test_state("burnin_test", "burning_in")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")
            assert exit_code == 0

            # Verify burning_in test was executed
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("burnin_test")
            assert len(history) >= 1, "burnin_test should have been executed"

    def test_burn_in_inclusion_regardless_of_hash(self):
        """Burn-in tests are included regardless of hash change status."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, ["stable_test"])

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.set_test_state("burnin_test", "burning_in")
            # Set hash for burning_in test - same hash means "unchanged"
            sf.set_target_hash("burnin_test", "same_hash")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            # Mock compute_target_hashes to return the same hash for burnin_test
            # (unchanged), but the burn-in inclusion should still add it
            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=True,  # Hash filtering active
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {
                    "stable_test": "new_hash",
                    "burnin_test": "same_hash",  # unchanged
                }
                exit_code = _run_regression(args, manifest, dag, "abc123")

            assert exit_code == 0

            # Verify burning_in test was executed despite unchanged hash
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("burnin_test")
            assert len(history) >= 1, (
                "burnin_test should be executed even with unchanged hash"
            )

    def test_burn_in_closure_includes_dependencies(self):
        """Dependency closure includes dependencies of burn-in tests."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            # dep_test is a dependency of burnin_test, not selected by co-occurrence
            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "dep_test": {
                        "assertion": "Dependency check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": ["dep_test"],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Graph only selects stable_test
            graph_path = self._make_graph(tmpdir, ["stable_test"])

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.set_test_state("dep_test", "stable")
            sf.set_test_state("burnin_test", "burning_in")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")
            assert exit_code == 0

            # Verify both burnin_test and its dependency dep_test were executed
            sf2 = StatusFile(status_path)
            burnin_history = sf2.get_test_history("burnin_test")
            dep_history = sf2.get_test_history("dep_test")
            assert len(burnin_history) >= 1, "burnin_test should have been executed"
            assert len(dep_history) >= 1, (
                "dep_test should be included via dependency closure"
            )

    def test_burn_in_no_status_file_no_inclusion(self):
        """Without status_file, no burn-in tests are added."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "new_test": {
                        "assertion": "New check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Graph only selects stable_test
            graph_path = self._make_graph(tmpdir, ["stable_test"])

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=None,  # No status file
                max_reruns=5,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, None)

            # Only stable_test runs, new_test is NOT included
            # exit 0 because stable_test passes
            assert exit_code == 0

    def test_burn_in_end_to_end_lifecycle_transition(self):
        """End-to-end: burning_in test included in regression, lifecycle transitions fire."""
        from orchestrator.main import _run_regression

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            graph_path = self._make_graph(tmpdir, ["stable_test"])

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(
                status_path,
                min_reliability=0.90,
                statistical_significance=0.90,
            )
            sf.set_test_state("stable_test", "stable")
            sf.set_test_state("burnin_test", "burning_in")
            # Seed burning_in test with prior passes so SPRT can decide
            for _ in range(50):
                sf.record_run("burnin_test", True, commit="prior")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="regression",
                changed_files="src/module.py",
                co_occurrence_graph=graph_path,
                max_parallel=1,
                max_failures=None,
                max_test_percentage=1.0,
                max_hops=2,
                skip_unchanged=False,
                status_file=status_path,
                max_reruns=5,
                min_reliability=0.90,
                statistical_significance=0.90,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            exit_code = _run_regression(args, manifest, dag, "abc123")
            assert exit_code == 0

            # Check lifecycle: burning_in test should have more evidence now
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("burnin_test")
            # Should have 50 prior + at least 1 new run
            assert len(history) > 50


class TestBurnInSweepInEffort:
    """Tests for burn-in sweep phase in effort converge/max modes (Step 5.2)."""

    def _make_script(self, tmpdir: str, name: str, exit_code: int = 0) -> str:
        """Create a test script."""
        script_path = Path(tmpdir) / f"{name}.sh"
        script_path.write_text(f"#!/bin/bash\nexit {exit_code}\n")
        script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
        return str(script_path)

    def test_effort_burnin_sweep_runs_for_burning_in_tests(self):
        """Converge mode runs burn-in sweep after SPRT rerun for burning_in tests."""
        from orchestrator.main import _run_effort

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.set_test_state("burnin_test", "burning_in")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="converge",
                max_parallel=1,
                max_failures=None,
                max_reruns=5,
                skip_unchanged=False,
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {
                    "stable_test": "hash_s",
                    "burnin_test": "hash_b",
                }
                exit_code = _run_effort(args, manifest, dag, "abc123")

            assert exit_code == 0

            # Verify burnin_test has evidence from sweep
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("burnin_test")
            # Should have initial run + effort reruns + sweep runs
            assert len(history) >= 2

    def test_effort_no_burnin_sweep_skipped(self):
        """Converge mode skips burn-in sweep when no burning_in tests exist."""
        from orchestrator.main import _run_effort

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="converge",
                max_parallel=1,
                max_failures=None,
                max_reruns=5,
                skip_unchanged=False,
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"stable_test": "hash_s"}
                exit_code = _run_effort(args, manifest, dag, "abc123")

            # All stable, all pass -> exit 0
            assert exit_code == 0

    def test_effort_burnin_sweep_promotes_to_stable(self):
        """Burn-in sweep in effort mode promotes test to stable after enough evidence."""
        from orchestrator.main import _run_effort

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("burnin_test", "burning_in")
            # Pre-populate with enough prior passes so sweep can decide
            for _ in range(20):
                sf.record_run("burnin_test", True, commit="prior")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="converge",
                max_parallel=1,
                max_failures=None,
                max_reruns=5,
                skip_unchanged=False,
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"burnin_test": "hash_b"}
                exit_code = _run_effort(args, manifest, dag, "abc123")

            assert exit_code == 0

            # After initial run + effort + sweep, test should be promoted
            sf2 = StatusFile(status_path)
            state = sf2.get_test_state("burnin_test")
            assert state == "stable", (
                f"Expected stable after burn-in sweep, got {state}"
            )

    def test_effort_max_mode_with_burnin(self):
        """Max mode also runs burn-in sweep for burning_in tests."""
        from orchestrator.main import _run_effort

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("burnin_test", "burning_in")
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="max",
                max_parallel=1,
                max_failures=None,
                max_reruns=5,
                skip_unchanged=False,
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"burnin_test": "hash_b"}
                exit_code = _run_effort(args, manifest, dag, "abc123")

            assert exit_code == 0

            # Verify burnin_test received sweep runs
            sf2 = StatusFile(status_path)
            history = sf2.get_test_history("burnin_test")
            assert len(history) >= 2

    def test_effort_skip_unchanged_keeps_burning_in(self):
        """skip_unchanged=True does not skip burning_in tests (they need evidence)."""
        from orchestrator.main import _run_effort

        with tempfile.TemporaryDirectory() as tmpdir:
            pass_script = self._make_script(tmpdir, "pass", 0)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "stable_test": {
                        "assertion": "Stable check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                    "burnin_test": {
                        "assertion": "Burn-in check",
                        "executable": pass_script,
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("stable_test", "stable")
            sf.set_target_hash("stable_test", "hash_s")  # unchanged
            sf.set_test_state("burnin_test", "burning_in")
            sf.set_target_hash("burnin_test", "hash_b")  # unchanged
            sf.save()

            dag = TestDAG.from_manifest(manifest)

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort="converge",
                max_parallel=1,
                max_failures=None,
                max_reruns=5,
                skip_unchanged=True,  # Hash filtering active
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
            )

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {
                    "stable_test": "hash_s",   # unchanged
                    "burnin_test": "hash_b",   # unchanged
                }
                exit_code = _run_effort(args, manifest, dag, "abc123")

            assert exit_code == 0

            # stable_test should be skipped (unchanged + conclusive)
            # burnin_test should NOT be skipped (burning_in needs evidence)
            sf2 = StatusFile(status_path)
            burnin_history = sf2.get_test_history("burnin_test")
            assert len(burnin_history) >= 1, (
                "burnin_test should have been executed despite unchanged hash"
            )


class TestFlakyDeadlineInOrchestrator:
    """Tests for flaky deadline auto-disable in _run_orchestrator."""

    def test_flaky_deadline_disables_before_execution(self):
        """Flaky tests exceeding deadline are disabled before execution."""
        from orchestrator.main import _run_orchestrator

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a passing test script
            script_path = Path(tmpdir) / "test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "good_test": {
                        "assertion": "Good check",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                    "flaky_old": {
                        "assertion": "Old flaky check",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Set up status file with an old flaky test
            status_path = Path(tmpdir) / "status.json"
            old_date = (
                datetime.datetime.now(tz=datetime.timezone.utc)
                - datetime.timedelta(days=20)
            ).isoformat()
            with open(status_path, "w") as f:
                json.dump(
                    {
                        "tests": {
                            "flaky_old": {
                                "state": "flaky",
                                "history": [],
                                "last_updated": old_date,
                            },
                        }
                    },
                    f,
                )

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort=None,
                max_parallel=1,
                max_failures=None,
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
                skip_unchanged=False,
            )

            with patch("orchestrator.main._resolve_git_context") as mock_git:
                mock_git.return_value = "abc123"
                exit_code = _run_orchestrator(args)

            # Should succeed (good_test passes, flaky_old is auto-disabled)
            assert exit_code == 0

            # Verify flaky_old is now disabled in status file
            sf = StatusFile(status_path)
            assert sf.get_test_state("flaky_old") == "disabled"

    def test_flaky_deadline_within_deadline_not_disabled(self):
        """Flaky tests within deadline remain flaky and execute normally."""
        from orchestrator.main import _run_orchestrator

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a passing test script
            script_path = Path(tmpdir) / "test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "recent_flaky": {
                        "assertion": "Recent flaky check",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            # Set up status file with a recently-flaky test
            status_path = Path(tmpdir) / "status.json"
            recent_date = (
                datetime.datetime.now(tz=datetime.timezone.utc)
                - datetime.timedelta(days=3)
            ).isoformat()
            with open(status_path, "w") as f:
                json.dump(
                    {
                        "tests": {
                            "recent_flaky": {
                                "state": "flaky",
                                "history": [],
                                "last_updated": recent_date,
                            },
                        }
                    },
                    f,
                )

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort=None,
                max_parallel=1,
                max_failures=None,
                status_file=status_path,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
                skip_unchanged=False,
            )

            with patch("orchestrator.main._resolve_git_context") as mock_git:
                mock_git.return_value = "abc123"
                _run_orchestrator(args)

            # Verify recent_flaky is still flaky (not auto-disabled)
            sf = StatusFile(status_path)
            assert sf.get_test_state("recent_flaky") == "flaky"

    def test_flaky_deadline_no_status_file_no_crash(self):
        """Without status_file, deadline check is skipped (no crash)."""
        from orchestrator.main import _run_orchestrator

        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = Path(tmpdir) / "test.sh"
            script_path.write_text("#!/bin/bash\nexit 0\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            manifest = {
                "test_set": {"name": "tests", "assertion": "test"},
                "test_set_tests": {
                    "a": {
                        "assertion": "check",
                        "executable": str(script_path),
                        "depends_on": [],
                    },
                },
            }
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))

            args = _make_args(
                manifest=manifest_path,
                mode="diagnostic",
                effort=None,
                max_parallel=1,
                max_failures=None,
                status_file=None,
                min_reliability=0.99,
                statistical_significance=0.95,
                allow_dirty=True,
                output=None,
                discover_workspace_tests=False,
                flaky_deadline_days=14,
                skip_unchanged=False,
            )

            exit_code = _run_orchestrator(args)
            assert exit_code == 0


class TestEffortConvergeRequiresStatusFile:
    """Tests for effort converge/max validation."""

    def test_converge_requires_status_file(self):
        """--effort converge without --status-file returns error."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests"},
                "test_set_tests": {},
            }))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--effort", "converge",
            ])
            assert exit_code == 1

    def test_max_requires_status_file(self):
        """--effort max without --status-file returns error."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text(json.dumps({
                "test_set": {"name": "tests"},
                "test_set_tests": {},
            }))

            exit_code = main([
                "--manifest", str(manifest_path),
                "--effort", "max",
            ])
            assert exit_code == 1


class TestResolveGitContext:
    """Tests for _resolve_git_context."""

    def test_returns_commit_sha_when_clean(self):
        """Returns HEAD sha when tree is clean."""
        with patch("orchestrator.main.subprocess.run") as mock_run:
            mock_run.side_effect = [
                # git rev-parse HEAD
                type("Result", (), {"returncode": 0, "stdout": "abc123\n"})(),
                # git status --porcelain
                type("Result", (), {"returncode": 0, "stdout": ""})(),
            ]
            sha = _resolve_git_context(allow_dirty=False)
            assert sha == "abc123"

    def test_allow_dirty_appends_dirty_suffix(self):
        """With allow_dirty=True and dirty tree, SHA gets -dirty suffix."""
        with patch("orchestrator.main.subprocess.run") as mock_run:
            mock_run.side_effect = [
                # git rev-parse HEAD
                type("Result", (), {"returncode": 0, "stdout": "def456\n"})(),
                # git status --porcelain (dirty)
                type("Result", (), {"returncode": 0, "stdout": " M file.py\n"})(),
            ]
            sha = _resolve_git_context(allow_dirty=True)
            assert sha == "def456-dirty"

    def test_allow_dirty_clean_tree_no_suffix(self):
        """With allow_dirty=True but clean tree, SHA has no suffix."""
        with patch("orchestrator.main.subprocess.run") as mock_run:
            mock_run.side_effect = [
                # git rev-parse HEAD
                type("Result", (), {"returncode": 0, "stdout": "def456\n"})(),
                # git status --porcelain (clean)
                type("Result", (), {"returncode": 0, "stdout": ""})(),
            ]
            sha = _resolve_git_context(allow_dirty=True)
            assert sha == "def456"

    def test_dirty_tree_exits(self):
        """Dirty working tree causes SystemExit when allow_dirty=False."""
        with patch("orchestrator.main.subprocess.run") as mock_run:
            mock_run.side_effect = [
                # git rev-parse HEAD
                type("Result", (), {"returncode": 0, "stdout": "abc123\n"})(),
                # git status --porcelain (dirty)
                type("Result", (), {"returncode": 0, "stdout": " M file.py\n"})(),
            ]
            with pytest.raises(SystemExit):
                _resolve_git_context(allow_dirty=False)

    def test_no_git_returns_none(self):
        """Returns None when git is not installed."""
        with patch("orchestrator.main.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError
            sha = _resolve_git_context(allow_dirty=False)
            assert sha is None

    def test_not_a_repo_returns_none(self):
        """Returns None when not inside a git repository."""
        with patch("orchestrator.main.subprocess.run") as mock_run:
            mock_run.return_value = type(
                "Result", (), {"returncode": 128, "stdout": "", "stderr": "not a git repo"}
            )()
            sha = _resolve_git_context(allow_dirty=False)
            assert sha is None


def _has_git_repo() -> bool:
    """Check if a git repository is available in the current directory."""
    import subprocess
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


class TestComputeAndFilterHashes:
    """Tests for _compute_and_filter_hashes function."""

    def _make_dag(self, test_names: list[str]) -> TestDAG:
        """Create a simple DAG with the given test names."""
        from orchestrator.execution.dag import TestNode

        dag = TestDAG()
        for name in test_names:
            dag.nodes[name] = TestNode(
                name=name,
                assertion=f"assert_{name}",
                executable=f"/bin/{name}",
            )
        return dag

    def test_all_new_hashes_treated_as_changed(self):
        """All tests with no stored hash are treated as changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "stable")
            sf.save()

            dag = self._make_dag(["//test:a", "//test:b"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {
                    "//test:a": "hash_a",
                    "//test:b": "hash_b",
                }
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == {"//test:a", "//test:b"}
            assert skippable == set()
            assert hashes == {"//test:a": "hash_a", "//test:b": "hash_b"}

    def test_unchanged_hash_stable_is_skippable(self):
        """Stable test with unchanged hash is skippable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash_a")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == set()
            assert skippable == {"//test:a"}

    def test_unchanged_hash_burning_in_not_skippable(self):
        """burning_in test with unchanged hash is NOT skippable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.set_target_hash("//test:a", "hash_a")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == set()
            assert skippable == set()

    def test_changed_hash_invalidates_evidence(self):
        """Changed hash triggers invalidate_evidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "old_hash")
            sf.record_run("//test:a", True, commit="c1", target_hash="old_hash")
            sf.record_run("//test:a", True, commit="c2", target_hash="old_hash")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "new_hash"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert "//test:a" in changed
            # Evidence should be invalidated
            assert sf.get_test_state("//test:a") == "burning_in"
            assert len(sf.get_test_history("//test:a")) == 0
            # Hash should be updated
            assert sf.get_target_hash("//test:a") == "new_hash"

    def test_skip_unchanged_false_no_skippable(self):
        """With skip_unchanged=False, no tests are skippable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash_a")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=False,
                )

            assert changed == set()
            assert skippable == set()

    def test_hash_computation_failure_fallback(self):
        """When hash computation fails, all tests treated as changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {}  # Empty = failure
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == {"//test:a"}
            assert skippable == set()
            assert hashes == {}

    def test_mixed_changed_and_unchanged(self):
        """Mix of changed and unchanged tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash_a")
            sf.set_test_state("//test:b", "stable")
            sf.set_target_hash("//test:b", "old_hash_b")
            sf.set_test_state("//test:c", "burning_in")
            sf.set_target_hash("//test:c", "hash_c")
            sf.save()

            dag = self._make_dag(["//test:a", "//test:b", "//test:c"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {
                    "//test:a": "hash_a",      # unchanged, stable -> skippable
                    "//test:b": "new_hash_b",  # changed
                    "//test:c": "hash_c",      # unchanged, burning_in -> not skippable
                }
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == {"//test:b"}
            assert skippable == {"//test:a"}

    def test_new_hash_for_existing_test(self):
        """Test with no stored hash gets hash set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            # No hash set
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert "//test:a" in changed
            # Hash should be stored
            assert sf.get_target_hash("//test:a") == "hash_a"
            # No invalidation since there was no previous hash
            assert sf.get_test_state("//test:a") == "stable"

    def test_label_missing_from_hash_result(self):
        """Test label not in hash result is treated as changed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.save()

            dag = self._make_dag(["//test:a", "//test:b"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                # Only returns hash for //test:a
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert "//test:b" in changed
            assert "//test:a" in changed  # new hash, no stored

    def test_flaky_unchanged_is_skippable(self):
        """Flaky test with unchanged hash is skippable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "flaky")
            sf.set_target_hash("//test:a", "hash_a")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == set()
            assert skippable == {"//test:a"}

    def test_new_test_unchanged_not_skippable(self):
        """new test with unchanged hash is NOT skippable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "new")
            sf.set_target_hash("//test:a", "hash_a")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                changed, skippable, hashes = _compute_and_filter_hashes(
                    dag, sf, skip_unchanged=True,
                )

            assert changed == set()
            assert skippable == set()

    def test_hash_saved_after_computation(self):
        """Status file is saved after hash computation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf = StatusFile(path)
            sf.set_test_state("//test:a", "stable")
            sf.save()

            dag = self._make_dag(["//test:a"])

            with patch(
                "orchestrator.execution.target_hash.compute_target_hashes"
            ) as mock_compute:
                mock_compute.return_value = {"//test:a": "hash_a"}
                _compute_and_filter_hashes(dag, sf, skip_unchanged=True)

            # Reload from disk to verify save was called
            sf2 = StatusFile(path)
            assert sf2.get_target_hash("//test:a") == "hash_a"


@pytest.mark.skipif(not _has_git_repo(), reason="No git repository available")
class TestBuildGraph:
    """Tests for build-graph subcommand."""

    def test_build_graph_from_git(self):
        """Build graph from actual git repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "graph.json"

            # Use the actual test_sets_bazel_rules repo
            args = _make_args(
                command="build-graph",
                output=output_path,
                source_extensions=".py,.bzl",
                test_patterns="*_test.*,test_*.*",
                max_history=50,
                repo_root=Path("."),
            )
            exit_code = cmd_build_graph(args)
            assert exit_code == 0
            assert output_path.exists()

            # Verify graph structure
            graph = json.loads(output_path.read_text())
            assert "metadata" in graph
            assert "file_commits" in graph
            assert "commit_files" in graph
            assert graph["metadata"]["total_commits_analyzed"] >= 0

    def test_build_graph_custom_extensions(self):
        """Build graph with custom source extensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "graph.json"

            args = _make_args(
                command="build-graph",
                output=output_path,
                source_extensions=".py",
                test_patterns=None,
                max_history=10,
                repo_root=Path("."),
            )
            exit_code = cmd_build_graph(args)
            assert exit_code == 0

    def test_build_graph_via_main(self):
        """build-graph subcommand accessible via main."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "graph.json"

            exit_code = main([
                "build-graph",
                "--output", str(output_path),
                "--max-history", "5",
            ])
            assert exit_code == 0
