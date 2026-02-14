"""Tests for co-occurrence graph construction."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.co_occurrence import (
    DEFAULT_SOURCE_EXTENSIONS,
    DEFAULT_TEST_PATTERNS,
    build_co_occurrence_graph,
    classify_file,
    load_graph,
    parse_git_log,
    save_graph,
)


class TestClassifyFile:
    """Tests for file classification."""

    def test_python_source(self):
        """Python file classified as source."""
        assert classify_file("src/main.py") == "source"

    def test_java_source(self):
        """Java file classified as source."""
        assert classify_file("src/Main.java") == "source"

    def test_go_source(self):
        """Go file classified as source."""
        assert classify_file("pkg/handler.go") == "source"

    def test_rust_source(self):
        """Rust file classified as source."""
        assert classify_file("src/lib.rs") == "source"

    def test_typescript_source(self):
        """TypeScript file classified as source."""
        assert classify_file("src/app.ts") == "source"

    def test_bzl_source(self):
        """Starlark file classified as source."""
        assert classify_file("rules/defs.bzl") == "source"

    def test_python_test_suffix(self):
        """Python test with _test suffix classified as test."""
        assert classify_file("src/main_test.py") == "test"

    def test_python_test_prefix(self):
        """Python test with test_ prefix classified as test."""
        assert classify_file("test_main.py") == "test"

    def test_spec_file(self):
        """Spec file classified as test."""
        assert classify_file("app_spec.ts") == "test"

    def test_test_takes_priority_over_source(self):
        """Test pattern matched before source extension check."""
        assert classify_file("auth_test.py") == "test"

    def test_markdown_ignored(self):
        """Markdown file classified as ignored."""
        assert classify_file("README.md") == "ignored"

    def test_json_ignored(self):
        """JSON file classified as ignored."""
        assert classify_file("package.json") == "ignored"

    def test_yaml_ignored(self):
        """YAML file classified as ignored."""
        assert classify_file("config.yaml") == "ignored"

    def test_dockerfile_ignored(self):
        """Dockerfile classified as ignored."""
        assert classify_file("Dockerfile") == "ignored"

    def test_gitignore_ignored(self):
        """Gitignore file classified as ignored."""
        assert classify_file(".gitignore") == "ignored"

    def test_custom_extensions(self):
        """Custom source extensions override defaults."""
        assert classify_file("main.rb", source_extensions=[".rb"]) == "source"
        assert classify_file("main.py", source_extensions=[".rb"]) == "ignored"

    def test_custom_patterns(self):
        """Custom test patterns override defaults."""
        assert classify_file("main_tests.py", test_patterns=["*_tests.*"]) == "test"

    def test_nested_path(self):
        """Classification works on nested file paths."""
        assert classify_file("a/b/c/d/main.py") == "source"
        assert classify_file("a/b/c/main_test.py") == "test"

    def test_no_extension(self):
        """File with no extension classified as ignored."""
        assert classify_file("Makefile") == "ignored"


class TestParseGitLog:
    """Tests for git log output parsing."""

    def test_single_commit(self):
        """Parse a single commit with files."""
        output = (
            "COMMIT abc123 2026-02-10T10:00:00+00:00\n"
            "\n"
            "src/main.py\n"
            "src/utils.py\n"
            "\n"
        )
        commits = parse_git_log(output)
        assert len(commits) == 1
        assert commits[0]["hash"] == "abc123"
        assert commits[0]["timestamp"] == "2026-02-10T10:00:00+00:00"
        assert commits[0]["files"] == ["src/main.py", "src/utils.py"]

    def test_multiple_commits(self):
        """Parse multiple commits."""
        output = (
            "COMMIT aaa111 2026-02-10T10:00:00+00:00\n"
            "\n"
            "src/a.py\n"
            "\n"
            "COMMIT bbb222 2026-02-09T10:00:00+00:00\n"
            "\n"
            "src/b.py\n"
            "src/c.py\n"
            "\n"
        )
        commits = parse_git_log(output)
        assert len(commits) == 2
        assert commits[0]["hash"] == "aaa111"
        assert commits[1]["hash"] == "bbb222"
        assert commits[1]["files"] == ["src/b.py", "src/c.py"]

    def test_empty_output(self):
        """Empty git output produces no commits."""
        commits = parse_git_log("")
        assert commits == []

    def test_commit_no_files(self):
        """Commit with no changed files."""
        output = (
            "COMMIT abc123 2026-02-10T10:00:00+00:00\n"
            "\n"
        )
        commits = parse_git_log(output)
        assert len(commits) == 1
        assert commits[0]["files"] == []

    def test_whitespace_handling(self):
        """Extra whitespace handled gracefully."""
        output = (
            "COMMIT abc123 2026-02-10T10:00:00+00:00\n"
            "  src/main.py  \n"
            "  tests/test_main.py  \n"
        )
        commits = parse_git_log(output)
        assert commits[0]["files"] == ["src/main.py", "tests/test_main.py"]


class TestBuildGraph:
    """Tests for co-occurrence graph construction."""

    def test_build_graph_basic(self):
        """Build graph from simple commit data."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/main.py", "tests/main_test.py"],
            },
        ]
        graph = build_co_occurrence_graph(commits)

        assert "src/main.py" in graph["file_commits"]
        assert "tests/main_test.py" in graph["file_commits"]
        assert "aaa" in graph["commit_files"]
        assert graph["commit_files"]["aaa"]["source_files"] == ["src/main.py"]
        assert graph["commit_files"]["aaa"]["test_files"] == ["tests/main_test.py"]

    def test_build_graph_multiple_commits(self):
        """Build graph from multiple commits."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/a.py", "tests/a_test.py"],
            },
            {
                "hash": "bbb",
                "timestamp": "2026-02-09T10:00:00Z",
                "files": ["src/a.py", "src/b.py"],
            },
        ]
        graph = build_co_occurrence_graph(commits)

        # src/a.py appears in both commits
        assert len(graph["file_commits"]["src/a.py"]) == 2
        assert len(graph["commit_files"]) == 2

    def test_build_graph_skip_no_source_files(self):
        """Commits with no source files are skipped."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["README.md", "docs/guide.txt"],
            },
        ]
        graph = build_co_occurrence_graph(commits)
        assert len(graph["commit_files"]) == 0
        assert len(graph["file_commits"]) == 0

    def test_build_graph_metadata(self):
        """Graph metadata is populated correctly."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/main.py"],
            },
        ]
        graph = build_co_occurrence_graph(commits)

        assert graph["metadata"]["last_commit"] == "aaa"
        assert graph["metadata"]["total_commits_analyzed"] == 1
        assert graph["metadata"]["source_extensions"] == DEFAULT_SOURCE_EXTENSIONS
        assert graph["metadata"]["test_patterns"] == DEFAULT_TEST_PATTERNS

    def test_build_graph_test_only_commit_skipped(self):
        """Commit with only test files (no source) is skipped."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["tests/a_test.py"],
            },
        ]
        graph = build_co_occurrence_graph(commits)
        assert len(graph["commit_files"]) == 0

    def test_build_graph_ignored_files_excluded(self):
        """Ignored files don't appear in the graph."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/main.py", "README.md", "Makefile"],
            },
        ]
        graph = build_co_occurrence_graph(commits)
        assert "README.md" not in graph["file_commits"]
        assert "Makefile" not in graph["file_commits"]
        assert "README.md" not in graph["commit_files"]["aaa"]["source_files"]

    def test_build_graph_custom_extensions(self):
        """Custom source extensions used for classification."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/main.rb", "src/utils.rb"],
            },
        ]
        graph = build_co_occurrence_graph(
            commits, source_extensions=[".rb"]
        )
        assert "src/main.rb" in graph["file_commits"]


class TestIncrementalUpdate:
    """Tests for incremental graph updates."""

    def test_incremental_new_commits_only(self):
        """Incremental update processes only new commits."""
        initial_commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/a.py"],
            },
        ]
        initial_graph = build_co_occurrence_graph(initial_commits)

        new_commits = [
            {
                "hash": "bbb",
                "timestamp": "2026-02-11T10:00:00Z",
                "files": ["src/b.py"],
            },
            # Include aaa again (should be skipped)
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/a.py"],
            },
        ]
        updated_graph = build_co_occurrence_graph(
            new_commits, existing_graph=initial_graph
        )

        assert "aaa" in updated_graph["commit_files"]
        assert "bbb" in updated_graph["commit_files"]
        # src/a.py should still have only 1 entry (not duplicated)
        assert len(updated_graph["file_commits"]["src/a.py"]) == 1
        assert updated_graph["metadata"]["total_commits_analyzed"] == 2

    def test_incremental_preserves_existing(self):
        """Incremental update preserves existing graph data."""
        initial_commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/a.py", "tests/a_test.py"],
            },
        ]
        initial_graph = build_co_occurrence_graph(initial_commits)

        new_commits = [
            {
                "hash": "bbb",
                "timestamp": "2026-02-11T10:00:00Z",
                "files": ["src/b.py"],
            },
        ]
        updated_graph = build_co_occurrence_graph(
            new_commits, existing_graph=initial_graph
        )

        # Original data preserved
        assert "src/a.py" in updated_graph["file_commits"]
        assert "tests/a_test.py" in updated_graph["file_commits"]
        assert "aaa" in updated_graph["commit_files"]

    def test_incremental_empty_new_commits(self):
        """Incremental update with no new commits preserves graph."""
        initial_commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/a.py"],
            },
        ]
        initial_graph = build_co_occurrence_graph(initial_commits)

        updated_graph = build_co_occurrence_graph(
            [], existing_graph=initial_graph
        )

        assert updated_graph["metadata"]["last_commit"] == "aaa"
        assert updated_graph["metadata"]["total_commits_analyzed"] == 1


class TestSaveLoadGraph:
    """Tests for graph serialization."""

    def test_save_and_load_roundtrip(self):
        """Graph survives save/load roundtrip."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/main.py", "tests/main_test.py"],
            },
        ]
        graph = build_co_occurrence_graph(commits)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.json"
            save_graph(graph, path)
            loaded = load_graph(path)

            assert loaded is not None
            assert loaded["commit_files"] == graph["commit_files"]
            assert loaded["file_commits"] == graph["file_commits"]

    def test_save_creates_directories(self):
        """Save creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "dir" / "graph.json"
            save_graph({"metadata": {}, "file_commits": {}, "commit_files": {}}, path)
            assert path.exists()

    def test_load_missing_file(self):
        """Load returns None for missing file."""
        result = load_graph("/nonexistent/path/graph.json")
        assert result is None

    def test_load_invalid_json(self):
        """Load returns None for invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.json"
            path.write_text("not json")
            result = load_graph(path)
            assert result is None


class TestEdgeCases:
    """Tests for edge cases in graph construction."""

    def test_empty_commits_list(self):
        """Empty commits list produces empty graph."""
        graph = build_co_occurrence_graph([])
        assert graph["commit_files"] == {}
        assert graph["file_commits"] == {}
        assert graph["metadata"]["total_commits_analyzed"] == 0

    def test_merge_commit_with_many_files(self):
        """Large commit with many files is handled."""
        files = [f"src/file_{i}.py" for i in range(100)]
        commits = [
            {
                "hash": "merge",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": files,
            },
        ]
        graph = build_co_occurrence_graph(commits)
        assert len(graph["file_commits"]) == 100
        assert len(graph["commit_files"]["merge"]["source_files"]) == 100

    def test_file_in_multiple_commits(self):
        """Same file appearing in multiple commits tracked correctly."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": ["src/shared.py"],
            },
            {
                "hash": "bbb",
                "timestamp": "2026-02-09T10:00:00Z",
                "files": ["src/shared.py"],
            },
            {
                "hash": "ccc",
                "timestamp": "2026-02-08T10:00:00Z",
                "files": ["src/shared.py"],
            },
        ]
        graph = build_co_occurrence_graph(commits)
        assert len(graph["file_commits"]["src/shared.py"]) == 3

    def test_commit_with_mixed_file_types(self):
        """Commit with source, test, and ignored files classified correctly."""
        commits = [
            {
                "hash": "aaa",
                "timestamp": "2026-02-10T10:00:00Z",
                "files": [
                    "src/main.py",
                    "tests/main_test.py",
                    "README.md",
                    "Dockerfile",
                ],
            },
        ]
        graph = build_co_occurrence_graph(commits)
        commit_data = graph["commit_files"]["aaa"]
        assert "src/main.py" in commit_data["source_files"]
        assert "tests/main_test.py" in commit_data["test_files"]
        assert "README.md" not in commit_data["source_files"]
        assert "Dockerfile" not in commit_data["source_files"]
