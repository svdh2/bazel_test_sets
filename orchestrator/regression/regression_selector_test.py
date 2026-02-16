"""Tests for regression test selection via co-occurrence analysis."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.regression.regression_selector import (
    RegressionConfig,
    SelectionResult,
    _days_since,
    _recency_weight,
    add_dependency_closure,
    resolve_test_label,
    select_regression_tests,
)


# Helper to create ISO timestamp strings at various ages
def _ts(days_ago: float = 0) -> str:
    """Create an ISO timestamp string for N days ago."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


# Sample manifest
SAMPLE_MANIFEST = {
    "test_set": {"name": "tests", "assertion": "Tests work"},
    "test_set_tests": {
        "auth_test": {
            "assertion": "Auth works",
            "executable": "tests/auth_test.py",
            "depends_on": [],
        },
        "billing_test": {
            "assertion": "Billing works",
            "executable": "tests/billing_test.py",
            "depends_on": ["auth_test"],
        },
        "checkout_test": {
            "assertion": "Checkout works",
            "executable": "tests/checkout_test.py",
            "depends_on": ["auth_test", "billing_test"],
        },
        "notification_test": {
            "assertion": "Notifications work",
            "executable": "tests/notification_test.py",
            "depends_on": [],
        },
        "report_test": {
            "assertion": "Reports work",
            "executable": "tests/report_test.py",
            "depends_on": [],
        },
    },
}


def _make_graph(file_commits=None, commit_files=None):
    """Create a minimal co-occurrence graph."""
    return {
        "metadata": {
            "last_commit": "latest",
            "total_commits_analyzed": 1,
            "source_extensions": [".py"],
            "test_patterns": ["*_test.*"],
        },
        "file_commits": file_commits or {},
        "commit_files": commit_files or {},
    }


class TestResolveTestLabel:
    """Tests for test file to label resolution."""

    def test_match_by_executable(self):
        """Match test file by executable path."""
        label = resolve_test_label("tests/auth_test.py", SAMPLE_MANIFEST)
        assert label == "auth_test"

    def test_match_by_basename(self):
        """Match test file by basename convention."""
        label = resolve_test_label("other/path/billing_test.py", SAMPLE_MANIFEST)
        assert label == "billing_test"

    def test_no_match(self):
        """No matching test returns None."""
        label = resolve_test_label("unknown_file.py", SAMPLE_MANIFEST)
        assert label is None

    def test_partial_name_match(self):
        """Partial name match in label."""
        label = resolve_test_label("some/report_test.py", SAMPLE_MANIFEST)
        assert label == "report_test"


class TestRecencyWeight:
    """Tests for recency decay computation."""

    def test_recency_present(self):
        """Present timestamp gets weight ~1.0."""
        w = _recency_weight(0.0, 180.0)
        assert abs(w - 1.0) < 0.001

    def test_recency_one_half_life(self):
        """Timestamp one half-life old gets weight ~0.5."""
        w = _recency_weight(180.0, 180.0)
        assert abs(w - 0.5) < 0.01

    def test_recency_two_half_lives(self):
        """Timestamp two half-lives old gets weight ~0.25."""
        w = _recency_weight(360.0, 180.0)
        assert abs(w - 0.25) < 0.01

    def test_recency_zero_half_life(self):
        """Zero half-life returns 1.0 (no decay)."""
        w = _recency_weight(100.0, 0.0)
        assert w == 1.0


class TestDaysSince:
    """Tests for timestamp age calculation."""

    def test_recent_timestamp(self):
        """Recent timestamp has small age."""
        ts = _ts(days_ago=1)
        days = _days_since(ts)
        assert 0.5 < days < 2.0

    def test_old_timestamp(self):
        """Old timestamp has large age."""
        ts = _ts(days_ago=30)
        days = _days_since(ts)
        assert 29 < days < 31

    def test_invalid_timestamp(self):
        """Invalid timestamp defaults to 365 days."""
        days = _days_since("not a timestamp")
        assert days == 365.0


class TestHop0DirectCoOccurrence:
    """Tests for direct co-occurrence at hop 0."""

    def test_hop_0_finds_direct_tests(self):
        """Changed file directly co-occurring with test is found."""
        graph = _make_graph(
            file_commits={
                "src/auth.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/auth.py"],
                    "test_files": ["tests/auth_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/auth.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=0),
        )

        assert "auth_test" in result.selected_tests
        assert result.scores["auth_test"] > 0

    def test_hop_0_no_match(self):
        """Changed file with no co-occurrence finds nothing."""
        graph = _make_graph()

        result = select_regression_tests(
            changed_files=["src/new_file.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=0, min_tests=0),
        )

        assert len(result.selected_tests) == 0
        assert not result.fallback_used


class TestTransitiveExpansion:
    """Tests for transitive expansion at hop 1+."""

    def test_transitive_hop_1(self):
        """Hop 1 discovers tests via intermediate source files."""
        graph = _make_graph(
            file_commits={
                "src/auth.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
                "src/billing.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                    {"commit": "c2", "timestamp": _ts(2)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/auth.py", "src/billing.py"],
                    "test_files": [],
                },
                "c2": {
                    "timestamp": _ts(2),
                    "source_files": ["src/billing.py"],
                    "test_files": ["tests/billing_test.py"],
                },
            },
        )

        # At hop 0: auth.py -> c1 -> no test files, discovers billing.py
        # At hop 1: billing.py -> c2 -> billing_test.py
        result = select_regression_tests(
            changed_files=["src/auth.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=2, min_tests=0),
        )

        assert "billing_test" in result.selected_tests

    def test_convergence_stops_early(self):
        """Expansion stops when no new files discovered."""
        graph = _make_graph(
            file_commits={
                "src/auth.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/auth.py"],
                    "test_files": ["tests/auth_test.py"],
                },
            },
        )

        # Only hop 0 has data; hop 1 finds no new source files
        result = select_regression_tests(
            changed_files=["src/auth.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=5, min_tests=0),
        )

        assert "auth_test" in result.selected_tests


class TestScoring:
    """Tests for score computation."""

    def test_recency_scoring(self):
        """Recent commits score higher than old commits."""
        graph = _make_graph(
            file_commits={
                "src/a.py": [
                    {"commit": "recent", "timestamp": _ts(1)},
                    {"commit": "old", "timestamp": _ts(365)},
                ],
            },
            commit_files={
                "recent": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/auth_test.py"],
                },
                "old": {
                    "timestamp": _ts(365),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/notification_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=0, min_tests=0, max_test_percentage=1.0),
        )

        assert result.scores["auth_test"] > result.scores["notification_test"]

    def test_hop_decay_scoring(self):
        """Tests found at hop 0 score higher than hop 1."""
        graph = _make_graph(
            file_commits={
                "src/a.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
                "src/b.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                    {"commit": "c2", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py", "src/b.py"],
                    "test_files": ["tests/auth_test.py"],
                },
                "c2": {
                    "timestamp": _ts(1),
                    "source_files": ["src/b.py"],
                    "test_files": ["tests/notification_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(
                max_hops=2,
                decay_per_hop=0.5,
                min_tests=0,
            ),
        )

        # auth_test found at hop 0, notification_test only via hop 1
        # Both should be found but auth_test should score higher
        if "auth_test" in result.scores and "notification_test" in result.scores:
            assert result.scores["auth_test"] >= result.scores["notification_test"]

    def test_frequency_accumulates(self):
        """Multiple co-occurrences accumulate score."""
        graph = _make_graph(
            file_commits={
                "src/a.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                    {"commit": "c2", "timestamp": _ts(2)},
                    {"commit": "c3", "timestamp": _ts(3)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/auth_test.py"],
                },
                "c2": {
                    "timestamp": _ts(2),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/auth_test.py"],
                },
                "c3": {
                    "timestamp": _ts(3),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/notification_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=0, min_tests=0, max_test_percentage=1.0),
        )

        # auth_test co-occurs in 2 commits, notification_test in 1
        assert result.scores["auth_test"] > result.scores["notification_test"]


class TestMaxPercentageCap:
    """Tests for max_test_percentage enforcement."""

    def test_max_percentage_limits_selection(self):
        """Selection respects max_test_percentage cap."""
        # Manifest has 5 tests, 10% = 0.5, ceil = 1 test max
        graph = _make_graph(
            file_commits={
                "src/a.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py"],
                    "test_files": [
                        "tests/auth_test.py",
                        "tests/billing_test.py",
                        "tests/notification_test.py",
                    ],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(
                max_test_percentage=0.10,
                max_hops=0,
                min_tests=0,
            ),
        )

        # Only 1 test should be selected (10% of 5 = 0.5, ceil = 1)
        # But dependency closure may add more
        selected_without_deps = [
            t for t, r in result.selection_reason.items()
            if "dependency" not in r and "fallback" not in r
        ]
        assert len(selected_without_deps) <= 1


class TestDependencyClosure:
    """Tests for dependency closure expansion."""

    def test_dependency_closure_adds_deps(self):
        """Dependency closure includes transitive dependencies."""
        closure = add_dependency_closure(["checkout_test"], SAMPLE_MANIFEST)
        assert "checkout_test" in closure
        assert "auth_test" in closure  # direct dep
        assert "billing_test" in closure  # direct dep

    def test_dependency_closure_no_deps(self):
        """Test with no dependencies returns just itself."""
        closure = add_dependency_closure(["auth_test"], SAMPLE_MANIFEST)
        assert closure == ["auth_test"]

    def test_dependency_closure_multiple(self):
        """Multiple selected tests merge their closures."""
        closure = add_dependency_closure(
            ["checkout_test", "notification_test"], SAMPLE_MANIFEST
        )
        assert "checkout_test" in closure
        assert "notification_test" in closure
        assert "auth_test" in closure
        assert "billing_test" in closure

    def test_dependency_closure_empty(self):
        """Empty selection returns empty list."""
        closure = add_dependency_closure([], SAMPLE_MANIFEST)
        assert closure == []

    def test_dependency_closure_in_selection(self):
        """Dependency closure is applied during test selection."""
        graph = _make_graph(
            file_commits={
                "src/checkout.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/checkout.py"],
                    "test_files": ["tests/checkout_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/checkout.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(
                max_test_percentage=1.0,
                max_hops=0,
                min_tests=0,
            ),
        )

        # checkout_test depends on auth_test and billing_test
        assert "checkout_test" in result.selected_tests
        assert "auth_test" in result.selected_tests
        assert "billing_test" in result.selected_tests


class TestFallback:
    """Tests for fallback to rdeps when co-occurrence is insufficient."""

    def test_fallback_triggered(self):
        """Fallback triggered when co-occurrence yields too few tests."""
        graph = _make_graph()

        result = select_regression_tests(
            changed_files=["src/unknown.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=3, max_hops=0),
        )

        assert result.fallback_used
        assert len(result.selected_tests) >= 3

    def test_no_fallback_when_enough_tests(self):
        """Fallback not triggered when co-occurrence yields enough."""
        graph = _make_graph(
            file_commits={
                "src/a.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py"],
                    "test_files": [
                        "tests/auth_test.py",
                        "tests/billing_test.py",
                        "tests/notification_test.py",
                        "tests/report_test.py",
                    ],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(
                max_test_percentage=1.0,
                min_tests=3,
                max_hops=0,
            ),
        )

        assert not result.fallback_used

    def test_fallback_min_tests_zero(self):
        """No fallback when min_tests is 0."""
        graph = _make_graph()

        result = select_regression_tests(
            changed_files=["src/unknown.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=0, max_hops=0),
        )

        assert not result.fallback_used


class TestSelectionResult:
    """Tests for SelectionResult metadata."""

    def test_result_includes_changed_files(self):
        """Result includes the changed files list."""
        graph = _make_graph()
        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=0, max_hops=0),
        )
        assert result.changed_files == ["src/a.py"]

    def test_result_total_stable_tests(self):
        """Result includes total stable test count."""
        graph = _make_graph()
        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=0, max_hops=0),
        )
        assert result.total_stable_tests == 5

    def test_result_selection_reasons(self):
        """Result includes selection reason for each test."""
        graph = _make_graph(
            file_commits={
                "src/a.py": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/auth_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(max_hops=0, min_tests=0),
        )

        assert "auth_test" in result.selection_reason
        assert "co-occurrence" in result.selection_reason["auth_test"]


class TestEmptyInputs:
    """Tests for edge cases with empty inputs."""

    def test_no_changed_files(self):
        """No changed files produces empty selection."""
        graph = _make_graph()
        result = select_regression_tests(
            changed_files=[],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=0, max_hops=0),
        )
        assert len(result.selected_tests) == 0

    def test_empty_graph(self):
        """Empty graph falls back if min_tests > 0."""
        graph = _make_graph()
        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=3),
        )
        assert result.fallback_used

    def test_empty_manifest(self):
        """Empty manifest produces empty selection."""
        graph = _make_graph()
        manifest = {"test_set_tests": {}}
        result = select_regression_tests(
            changed_files=["src/a.py"],
            graph=graph,
            manifest=manifest,
            config=RegressionConfig(min_tests=0),
        )
        assert len(result.selected_tests) == 0

    def test_non_source_changed_files(self):
        """Changed files that are not source files are ignored."""
        graph = _make_graph(
            file_commits={
                "README.md": [
                    {"commit": "c1", "timestamp": _ts(1)},
                ],
            },
            commit_files={
                "c1": {
                    "timestamp": _ts(1),
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/auth_test.py"],
                },
            },
        )

        result = select_regression_tests(
            changed_files=["README.md"],
            graph=graph,
            manifest=SAMPLE_MANIFEST,
            config=RegressionConfig(min_tests=0, max_hops=0),
        )

        # README.md is not a source file, so no co-occurrence found
        assert len(result.selected_tests) == 0
