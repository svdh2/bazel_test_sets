"""Unit tests for the CI tool."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from ci_tool.main import (
    cmd_build_graph,
    cmd_burn_in,
    cmd_deflake,
    cmd_re_judge,
    cmd_test_status,
    main,
    parse_args,
)
from orchestrator.analysis.measurements import store_measurements
from orchestrator.lifecycle.status import StatusFile


def _make_args(**kwargs):
    """Create a simple namespace for testing."""
    import argparse
    return argparse.Namespace(**kwargs)


class TestParseArgs:
    """Tests for argument parsing."""

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
            entry = sf2.get_test_entry("//test:a")
            assert entry is not None
            assert entry["runs"] == 0
            assert entry["passes"] == 0

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
            sf.set_test_state("//test:a", "burning_in", runs=5, passes=5)
            sf.save()

            args = _make_args(
                command="burn-in",
                status_file=status_path,
                tests=["//test:a"],
            )
            exit_code = cmd_burn_in(args)
            assert exit_code == 0

            sf2 = StatusFile(status_path)
            entry = sf2.get_test_entry("//test:a")
            assert entry is not None
            assert entry["runs"] == 5  # Unchanged

    def test_burn_in_stable_rejected(self):
        """Burn-in of stable test is rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)
            sf.set_test_state("//test:a", "stable", runs=50, passes=50)
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
            sf.set_test_state("//test:a", "disabled", runs=0, passes=0)
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
            sf.set_test_state("//test:a", "burning_in", runs=5, passes=5)
            sf.set_test_state("//test:b", "stable", runs=50, passes=50)
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
            sf.set_test_state("//test:a", "flaky", runs=30, passes=25)
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
            entry = sf2.get_test_entry("//test:a")
            assert entry is not None
            assert entry["runs"] == 0
            assert entry["passes"] == 0

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
            sf.set_test_state("//test:a", "stable", runs=50, passes=50)
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
            sf.set_test_state("//test:a", "disabled", runs=0, passes=0)
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
            sf.set_test_state("//test:a", "flaky", runs=20, passes=15)
            sf.set_test_state("//test:b", "flaky", runs=30, passes=25)
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
            sf.set_test_state("//test:a", "stable", runs=50, passes=50)
            sf.set_test_state("//test:b", "burning_in", runs=10, passes=10)
            sf.set_test_state("//test:c", "flaky", runs=20, passes=15)
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
            sf.set_test_state("//test:a", "stable", runs=50, passes=50)
            sf.set_test_state("//test:b", "flaky", runs=20, passes=15)
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

    def test_no_command(self):
        """No command shows help and returns 1."""
        with pytest.raises(SystemExit):
            main([])

    def test_burn_in_via_main(self):
        """burn-in subcommand accessible via main."""
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
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "graph.json"

            exit_code = main([
                "build-graph",
                "--output", str(output_path),
                "--max-history", "5",
            ])
            assert exit_code == 0
