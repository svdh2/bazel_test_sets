"""Tests for the orchestrator main entry point, focused on the effort option."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.main import (
    _filter_manifest,
    _get_changed_files,
    _resolve_git_context,
    parse_args,
)


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

            # Create config with max_parallel=1
            config_path = Path(tmpdir) / ".test_set_config"
            config_path.write_text(json.dumps({"max_parallel": 1}))

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
                "--config-file", str(config_path),
            ])
            assert exit_code == 0

    def test_regression_detection_with_changed_files(self):
        """--effort regression with detection mode runs selected tests."""
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

            # Create config with max_parallel=1
            config_path = Path(tmpdir) / ".test_set_config"
            config_path.write_text(json.dumps({"max_parallel": 1}))

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
                "--config-file", str(config_path),
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
        """--effort converge without status_file in config returns error."""
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
        """--effort max without status_file in config returns error."""
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
