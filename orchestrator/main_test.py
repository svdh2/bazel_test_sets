"""Tests for the orchestrator main entry point, focused on the regression option."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.main import _filter_manifest, _get_changed_files, parse_args


class TestParseArgsRegression:
    """Tests for regression option argument parsing."""

    def test_regression_flag_accepted(self):
        """--regression is a valid flag combinable with any mode."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--mode", "diagnostic",
            "--regression",
            "--diff-base", "main",
        ])
        assert args.mode == "diagnostic"
        assert args.regression is True
        assert args.diff_base == "main"

    def test_regression_with_detection_mode(self):
        """--regression combines with --mode=detection."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--mode", "detection",
            "--regression",
            "--diff-base", "main",
        ])
        assert args.mode == "detection"
        assert args.regression is True

    def test_regression_default_false(self):
        """--regression defaults to False."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.regression is False

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

    def test_max_test_percentage_flag(self):
        """--max-test-percentage flag parsed correctly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-test-percentage", "0.25",
        ])
        assert args.max_test_percentage == 0.25

    def test_max_hops_flag(self):
        """--max-hops flag parsed correctly."""
        args = parse_args([
            "--manifest", "/path/manifest.json",
            "--max-hops", "3",
        ])
        assert args.max_hops == 3

    def test_default_values(self):
        """Default values for regression flags."""
        args = parse_args(["--manifest", "/path/manifest.json"])
        assert args.diff_base is None
        assert args.changed_files is None
        assert args.regression is False
        assert args.co_occurrence_graph == Path(".tests/co_occurrence_graph.json")
        assert args.max_test_percentage == 0.10
        assert args.max_hops == 2


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


class TestRegressionOptionMissingGraph:
    """Tests for regression option with missing co-occurrence graph."""

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
                "--regression",
                "--changed-files", "src/a.py",
                "--co-occurrence-graph", str(Path(tmpdir) / "nonexistent.json"),
            ])
            assert exit_code == 1

    def test_regression_no_diff_base_or_changed_files(self):
        """--regression without --diff-base or --changed-files errors."""
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
                "--regression",
                "--co-occurrence-graph", str(graph_path),
            ])
            assert exit_code == 1


class TestRegressionOptionEndToEnd:
    """End-to-end tests for regression option."""

    def test_regression_diagnostic_with_changed_files(self):
        """--regression with diagnostic mode runs selected tests."""
        from orchestrator.main import main
        from orchestrator.regression.co_occurrence import save_graph, build_co_occurrence_graph

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple pass-script
            import stat
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
                "--regression",
                "--changed-files", "src/auth.py",
                "--co-occurrence-graph", str(graph_path),
                "--max-parallel", "1",
            ])
            assert exit_code == 0

    def test_regression_detection_with_changed_files(self):
        """--regression with detection mode runs selected tests."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple pass-script
            import stat
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
                "--regression",
                "--changed-files", "src/auth.py",
                "--co-occurrence-graph", str(graph_path),
                "--max-parallel", "1",
            ])
            assert exit_code == 0

    def test_regression_no_changed_files_returns_zero(self):
        """--regression with empty changed files returns 0."""
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
                "--regression",
                "--changed-files", "",
                "--co-occurrence-graph", str(graph_path),
            ])
            assert exit_code == 0
