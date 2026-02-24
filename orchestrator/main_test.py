"""Tests for the orchestrator main entry point."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.analysis.measurements import store_measurements
from orchestrator.lifecycle.status import StatusFile
from orchestrator.main import (
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
