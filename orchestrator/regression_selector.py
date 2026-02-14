"""Regression test selection via co-occurrence graph analysis.

Selects a subset of stable tests most likely to catch regressions for a
given code change by analyzing commit co-occurrence history with bounded
transitive expansion, hop-aware scoring, dependency closure, and fallback
to Bazel rdeps when co-occurrence is insufficient.
"""

from __future__ import annotations

import math
import os
import subprocess
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RegressionConfig:
    """Configuration for regression test selection."""

    max_test_percentage: float = 0.10
    max_hops: int = 2
    decay_per_hop: float = 0.5
    recency_half_life_days: float = 180.0
    min_tests: int = 3
    source_extensions: list[str] = field(default_factory=lambda: [
        ".py", ".java", ".cc", ".go", ".rs", ".ts", ".js", ".bzl",
    ])


@dataclass
class SelectionResult:
    """Result of regression test selection."""

    selected_tests: list[str]
    scores: dict[str, float]
    selection_reason: dict[str, str]  # test -> reason
    fallback_used: bool
    total_stable_tests: int
    changed_files: list[str]


def _days_since(timestamp_str: str) -> float:
    """Calculate days between a timestamp and now.

    Args:
        timestamp_str: ISO 8601 timestamp string.

    Returns:
        Number of days since the timestamp (float).
    """
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 365.0  # Default to 1 year for unparseable timestamps

    now = datetime.now(timezone.utc)
    delta = now - dt
    return max(0.0, delta.total_seconds() / 86400.0)


def _recency_weight(age_days: float, half_life_days: float) -> float:
    """Compute exponential recency decay weight.

    Args:
        age_days: Age in days.
        half_life_days: Half-life for decay.

    Returns:
        Weight in [0, 1], where 1 = present, 0.5 = one half-life old.
    """
    if half_life_days <= 0:
        return 1.0
    return math.exp(-0.693 * age_days / half_life_days)


def resolve_test_label(
    test_file_path: str,
    manifest: dict[str, Any],
) -> str | None:
    """Map a test file path to a test label in the manifest.

    Strategy 1: Match by executable path in test targets.
    Strategy 2: Convention-based matching (filename without extension).

    Args:
        test_file_path: File path from git history.
        manifest: Parsed manifest dict.

    Returns:
        Matching test label, or None if no match found.
    """
    test_set_tests = manifest.get("test_set_tests", {})

    # Strategy 1: Match by executable path
    for label, info in test_set_tests.items():
        executable = info.get("executable", "")
        if executable and test_file_path in executable:
            return label

    # Strategy 2: Convention-based matching
    # e.g., "tests/payments/checkout_test.py" -> basename "checkout_test"
    basename = os.path.splitext(os.path.basename(test_file_path))[0]
    if basename in test_set_tests:
        return basename

    # Try matching against test names more loosely
    for label in test_set_tests:
        if basename and basename in label:
            return label

    return None


def _get_stable_test_labels(manifest: dict[str, Any]) -> set[str]:
    """Get all test labels from the manifest.

    In a full implementation this would filter by burn-in state, but for
    the core algorithm we treat all tests in the manifest as candidates.

    Args:
        manifest: Parsed manifest dict.

    Returns:
        Set of all test labels.
    """
    return set(manifest.get("test_set_tests", {}).keys())


def add_dependency_closure(
    selected_tests: list[str],
    manifest: dict[str, Any],
) -> list[str]:
    """Transitively include all dependencies of selected tests.

    BFS through depends_on edges, adding all reachable tests to ensure
    the selected subset is self-contained with respect to the DAG.

    Args:
        selected_tests: Initially selected test labels.
        manifest: Parsed manifest dict.

    Returns:
        Extended list including all transitive dependencies.
    """
    test_set_tests = manifest.get("test_set_tests", {})
    closure: set[str] = set(selected_tests)
    queue: deque[str] = deque(selected_tests)

    while queue:
        test = queue.popleft()
        test_data = test_set_tests.get(test, {})
        for dep in test_data.get("depends_on", []):
            if dep not in closure and dep in test_set_tests:
                closure.add(dep)
                queue.append(dep)

    return list(closure)


def select_regression_tests(
    changed_files: list[str],
    graph: dict[str, Any],
    manifest: dict[str, Any],
    config: RegressionConfig | None = None,
) -> SelectionResult:
    """Select regression tests using co-occurrence graph analysis.

    Performs bounded BFS expansion through the co-occurrence graph,
    scoring tests by frequency, recency, and hop decay. Adds dependency
    closure and falls back to manual selection when too few tests found.

    Args:
        changed_files: List of changed file paths.
        graph: Co-occurrence graph from build_co_occurrence_graph.
        manifest: Parsed manifest dict with test_set_tests.
        config: Regression configuration (uses defaults if None).

    Returns:
        SelectionResult with selected tests, scores, and metadata.
    """
    if config is None:
        config = RegressionConfig()

    all_stable_tests = _get_stable_test_labels(manifest)
    max_tests = max(1, math.ceil(len(all_stable_tests) * config.max_test_percentage))

    file_commits = graph.get("file_commits", {})
    commit_files = graph.get("commit_files", {})

    candidate_scores: dict[str, float] = {}
    selection_reason: dict[str, str] = {}
    visited_files: set[str] = set()
    frontier_files: set[str] = set()

    # Seed frontier with changed source files
    for f in changed_files:
        _, ext = os.path.splitext(f)
        if ext in config.source_extensions:
            frontier_files.add(f)
    visited_files = set(frontier_files)

    for hop in range(config.max_hops + 1):
        hop_decay = config.decay_per_hop ** hop
        new_source_files: set[str] = set()

        # Find all commits touching frontier files
        related_commits: set[str] = set()
        for f in frontier_files:
            if f in file_commits:
                for entry in file_commits[f]:
                    commit_hash = entry.get("commit", "")
                    if commit_hash:
                        related_commits.add(commit_hash)

        # From those commits, collect tests and new source files
        for commit_hash in related_commits:
            commit_data = commit_files.get(commit_hash, {})
            timestamp = commit_data.get("timestamp", "")
            age_days = _days_since(timestamp)
            recency = _recency_weight(age_days, config.recency_half_life_days)

            # Score test files
            for test_file in commit_data.get("test_files", []):
                test_label = resolve_test_label(test_file, manifest)
                if test_label is not None and test_label in all_stable_tests:
                    score = hop_decay * recency
                    candidate_scores[test_label] = (
                        candidate_scores.get(test_label, 0.0) + score
                    )
                    if test_label not in selection_reason:
                        selection_reason[test_label] = (
                            f"co-occurrence hop {hop}"
                        )

            # Discover new source files for next hop
            for source_file in commit_data.get("source_files", []):
                if source_file not in visited_files:
                    new_source_files.add(source_file)

        # Stopping conditions
        if len(candidate_scores) >= max_tests:
            break
        if not new_source_files:
            break

        # Prepare next hop
        visited_files |= new_source_files
        frontier_files = new_source_files

    # Rank by score, select top-N
    ranked = sorted(
        candidate_scores.items(), key=lambda x: x[1], reverse=True
    )
    selected = [test for test, _score in ranked[:max_tests]]
    scores = {test: score for test, score in ranked[:max_tests]}

    # Add dependency closure
    selected_with_deps = add_dependency_closure(selected, manifest)
    for dep in selected_with_deps:
        if dep not in scores:
            scores[dep] = 0.0
            selection_reason[dep] = "dependency closure"

    # Fallback: if too few tests, include all manifest tests
    fallback_used = False
    if len(selected_with_deps) < config.min_tests and all_stable_tests:
        fallback_used = True
        for test in all_stable_tests:
            if test not in selected_with_deps:
                selected_with_deps.append(test)
                if test not in scores:
                    scores[test] = 0.0
                if test not in selection_reason:
                    selection_reason[test] = "fallback (insufficient co-occurrence)"

    # Clean up selection_reason to only include actually selected tests
    final_selected = set(selected_with_deps)
    selection_reason = {k: v for k, v in selection_reason.items() if k in final_selected}

    return SelectionResult(
        selected_tests=selected_with_deps,
        scores=scores,
        selection_reason=selection_reason,
        fallback_used=fallback_used,
        total_stable_tests=len(all_stable_tests),
        changed_files=changed_files,
    )
