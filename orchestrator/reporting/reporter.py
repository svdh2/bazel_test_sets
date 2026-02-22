"""Report generation for test execution results.

Generates JSON reports from test results, supporting the six-status model:
passed, failed, dependencies_failed, passed+dependencies_failed,
failed+dependencies_failed, not_run.

Supports hierarchical reports mirroring the DAG structure, burn-in
progress, regression selection details, and rolling history for
reverse-chronological SPRT.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from orchestrator.execution.executor import TestResult


# Valid status values in the six-status model
VALID_STATUSES = frozenset({
    "passed",
    "failed",
    "dependencies_failed",
    "passed+dependencies_failed",
    "failed+dependencies_failed",
    "not_run",
})

# Maximum rolling history entries per test
MAX_HISTORY = 500


class Reporter:
    """Collects test results and generates JSON reports.

    The reporter accepts TestResult objects and produces a structured
    JSON report file containing all results with timing, status, and
    log information. Supports hierarchical DAG structure, burn-in
    progress, regression selection, and rolling history.
    """

    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self.manifest: dict[str, Any] | None = None
        self.commit_hash: str | None = None
        self.burn_in_data: dict[str, dict[str, Any]] = {}
        self.regression_selection: dict[str, Any] | None = None
        self.inferred_deps: dict[str, list[dict[str, Any]]] = {}
        self.e_value_verdict: dict[str, Any] | None = None
        self.effort_data: dict[str, Any] | None = None
        self.lifecycle_data: dict[str, dict[str, Any]] = {}
        self.lifecycle_config: dict[str, Any] | None = None
        self.reliability_demoted_tests: list[str] = []
        self.source_link_base: str | None = None

    def set_manifest(self, manifest: dict[str, Any]) -> None:
        """Set the manifest for hierarchical report generation.

        Args:
            manifest: Parsed manifest dict with test_set and test_set_tests.
        """
        self.manifest = manifest

    def set_commit_hash(self, commit_hash: str) -> None:
        """Set the commit hash to tag results with.

        Args:
            commit_hash: Git commit hash string.
        """
        self.commit_hash = commit_hash

    def add_burn_in_progress(
        self, test_name: str, progress: dict[str, Any]
    ) -> None:
        """Add burn-in progress data for a test.

        Args:
            test_name: Test label.
            progress: Dict with runs, passes, sprt_status keys.
        """
        self.burn_in_data[test_name] = progress

    def set_regression_selection(
        self, selection_data: dict[str, Any]
    ) -> None:
        """Set regression selection details for the report.

        Args:
            selection_data: Dict with changed_files, scores, etc.
        """
        self.regression_selection = selection_data

    def add_inferred_dependencies(
        self, test_name: str, deps: list[dict[str, Any]]
    ) -> None:
        """Add inferred rigging dependencies for a test.

        Args:
            test_name: Test label.
            deps: List of inferred dependency dicts.
        """
        self.inferred_deps[test_name] = deps

    def set_e_value_verdict(
        self, verdict_data: dict[str, Any]
    ) -> None:
        """Set E-value test set verdict data for the report.

        Args:
            verdict_data: Dict from ``verdict_to_dict()`` with verdict,
                e_set, per_test, etc.
        """
        self.e_value_verdict = verdict_data

    def set_effort_data(self, data: dict[str, Any]) -> None:
        """Set effort mode classification data for the report.

        Args:
            data: Dict with mode, total_reruns, max_reruns_per_test,
                and classifications.
        """
        self.effort_data = data

    def set_lifecycle_data(
        self, data: dict[str, dict[str, Any]]
    ) -> None:
        """Set lifecycle state data for all tests.

        Args:
            data: Dict mapping test_name to {state}.
                The runs/passes/reliability fields are computed from
                rolling history by generate_report_with_history().
        """
        self.lifecycle_data = data

    def set_lifecycle_config(self, config: dict[str, Any]) -> None:
        """Set lifecycle configuration for the report.

        Args:
            config: Dict with min_reliability and
                statistical_significance.
        """
        self.lifecycle_config = config

    def set_source_link_base(self, base: str | None) -> None:
        """Set the source link base URL for source code links in the report.

        Args:
            base: GitHub blob URL prefix (e.g.
                ``https://github.com/owner/repo/blob/<sha>``) or
                ``None`` for local file path display.
        """
        self.source_link_base = base

    def add_result(self, result: TestResult) -> None:
        """Add a test result to the report.

        Args:
            result: TestResult object from test execution.
        """
        self.results.append(result)

    def add_results(self, results: list[TestResult]) -> None:
        """Add multiple test results to the report.

        Args:
            results: List of TestResult objects.
        """
        self.results.extend(results)

    def generate_report(self) -> dict[str, Any]:
        """Generate the report data structure.

        If a manifest is set, generates a hierarchical report mirroring
        the DAG structure. Otherwise, generates a flat report.

        Returns:
            Dictionary representing the full report, suitable for
            JSON serialization.
        """
        summary = self._compute_summary()
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        report: dict[str, Any] = {
            "generated_at": now,
            "summary": summary,
        }

        if self.commit_hash:
            report["commit"] = self.commit_hash

        if self.source_link_base is not None:
            report["source_link_base"] = self.source_link_base

        if self.manifest:
            report["test_set"] = self._build_hierarchical_report()
        else:
            report["tests"] = [self._format_result(r) for r in self.results]

        if self.regression_selection:
            report["regression_selection"] = self.regression_selection

        if self.e_value_verdict:
            report["e_value_verdict"] = self.e_value_verdict

        if self.effort_data:
            report["effort"] = self.effort_data

        if self.lifecycle_config:
            report["lifecycle_config"] = self.lifecycle_config

        return {"report": report}

    def generate_report_with_history(
        self, existing_report_path: Path | None = None,
    ) -> dict[str, Any]:
        """Generate report with rolling history appended.

        Reads an existing report, extracts per-test history, appends
        current results, and trims to MAX_HISTORY entries.

        Args:
            existing_report_path: Path to existing JSON report (optional).

        Returns:
            Report dict with history included.
        """
        report = self.generate_report()

        # Load existing history
        existing_history: dict[str, list[dict[str, Any]]] = {}
        if existing_report_path and existing_report_path.exists():
            try:
                with open(existing_report_path) as f:
                    existing = json.load(f)
                if existing and "report" in existing:
                    existing_history = existing["report"].get("history", {})
            except (json.JSONDecodeError, OSError):
                pass

        # Append current results to history
        history: dict[str, list[dict[str, Any]]] = dict(existing_history)
        for result in self.results:
            if result.name not in history:
                history[result.name] = []

            entry = {
                "status": result.status,
                "duration_seconds": round(result.duration, 3),
                "timestamp": report["report"]["generated_at"],
            }
            if self.commit_hash:
                entry["commit"] = self.commit_hash

            history[result.name].append(entry)

            # Trim to MAX_HISTORY
            if len(history[result.name]) > MAX_HISTORY:
                history[result.name] = history[result.name][-MAX_HISTORY:]

        report["report"]["history"] = history

        # Update lifecycle reliability from accumulated history so the
        # displayed percentage matches the visible timeline.  StatusFile
        # counters reset on lifecycle transitions, but the rolling report
        # history accumulates across all runs.
        if self.lifecycle_data:
            self._update_lifecycle_from_history(report["report"], history)

        return report

    def _update_lifecycle_from_history(
        self,
        report_inner: dict[str, Any],
        history: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Update lifecycle reliability using accumulated history.

        StatusFile counters reset on lifecycle transitions, but the
        rolling report history accumulates across all runs.  This method
        recomputes reliability from history so the displayed percentage
        matches the visible timeline.
        """
        # Compute reliability from history for each test
        history_reliability: dict[str, dict[str, int | float]] = {}
        for test_name, entries in history.items():
            runs = 0
            passes = 0
            for e in entries:
                status = e.get("status", "")
                if status in ("passed", "passed+dependencies_failed"):
                    runs += 1
                    passes += 1
                elif status in ("failed", "failed+dependencies_failed"):
                    runs += 1
                # dependencies_failed: test wasn't run, skip
            history_reliability[test_name] = {
                "runs": runs,
                "passes": passes,
                "reliability": round(passes / runs, 6) if runs > 0 else 0.0,
            }

        # Walk the test_set tree and update lifecycle entries
        test_set = report_inner.get("test_set")
        if test_set:
            self._update_node_lifecycle(test_set, history_reliability)

    def _update_node_lifecycle(
        self,
        node: dict[str, Any],
        history_reliability: dict[str, dict[str, int | float]],
    ) -> None:
        """Recursively update lifecycle data in a report node.

        Updates direct test entries first, then recurses into child
        subsets so their summaries are recomputed bottom-up.

        When a test's rolling reliability falls below min_reliability,
        its lifecycle state is overridden to ``flaky`` and the test
        counts as failed for test-set status aggregation.
        """
        min_rel = (self.lifecycle_config or {}).get("min_reliability", 1.0)

        # Update direct test entries
        tests = node.get("tests", {})
        for test_name, test_data in tests.items():
            lifecycle = test_data.get("lifecycle")
            if lifecycle and test_name in history_reliability:
                hr = history_reliability[test_name]
                lifecycle["runs"] = hr["runs"]
                lifecycle["passes"] = hr["passes"]
                lifecycle["reliability"] = hr["reliability"]
                # Override state to flaky if reliability is below threshold
                if (
                    lifecycle.get("state") != "disabled"
                    and hr["runs"] > 0
                    and hr["reliability"] < min_rel
                ):
                    lifecycle["state"] = "flaky"
                    self.reliability_demoted_tests.append(test_name)

        # Recurse into subsets (so their summaries and statuses update first)
        for subset in node.get("subsets", []):
            self._update_node_lifecycle(subset, history_reliability)

        # Recompute this node's summary from updated children
        lifecycle_summary = self._compute_lifecycle_summary(
            tests, node.get("subsets", []),
        )
        if lifecycle_summary is not None:
            node["lifecycle_summary"] = lifecycle_summary
        elif "lifecycle_summary" in node:
            del node["lifecycle_summary"]

        # Re-aggregate status: tests below min_reliability count as failed
        direct_statuses: list[str] = []
        for test_name, test_data in tests.items():
            if "status" not in test_data:
                continue
            lifecycle = test_data.get("lifecycle")
            if (
                lifecycle
                and test_name in history_reliability
                and lifecycle.get("state") != "disabled"
                and history_reliability[test_name]["runs"] > 0
                and history_reliability[test_name]["reliability"] < min_rel
            ):
                direct_statuses.append("failed")
            else:
                direct_statuses.append(test_data["status"])
        subset_statuses = [
            s.get("status", "no_tests") for s in node.get("subsets", [])
        ]
        node["status"] = _aggregate_status(direct_statuses + subset_statuses)

    def write_report(self, path: Path) -> None:
        """Write the report as a JSON file.

        Args:
            path: File path to write the JSON report to.
        """
        report = self.generate_report()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(report, f, indent=2)

    def write_report_with_history(
        self, path: Path, existing_path: Path | None = None,
    ) -> None:
        """Write report with rolling history as JSON.

        Args:
            path: File path to write.
            existing_path: Path to existing report for history (optional).
        """
        report = self.generate_report_with_history(existing_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(report, f, indent=2)

    def _build_hierarchical_report(self) -> dict[str, Any]:
        """Build a hierarchical report mirroring the DAG structure.

        If the manifest contains a tree structure (with ``subsets``),
        produces a nested report.  Otherwise falls back to a flat report
        for backward compatibility with older manifests.

        Returns:
            Nested dict with test_set at top, subsets recursively nested.
        """
        assert self.manifest is not None

        test_set_info = self.manifest.get("test_set", {})
        test_set_tests = self.manifest.get("test_set_tests", {})

        # Build results lookup
        results_by_name: dict[str, TestResult] = {}
        for r in self.results:
            results_by_name[r.name] = r

        # New tree-aware path
        if "subsets" in test_set_info:
            return self._build_report_node(
                test_set_info, test_set_tests, results_by_name,
            )

        # Fallback: old flat manifest (no subsets field)
        return self._build_flat_report_node(
            test_set_info, test_set_tests, results_by_name,
        )

    def _build_report_node(
        self,
        tree_node: dict[str, Any],
        test_set_tests: dict[str, dict[str, Any]],
        results_by_name: dict[str, TestResult],
    ) -> dict[str, Any]:
        """Recursively build a report node from a manifest tree node."""
        # Direct test entries
        test_entries: dict[str, dict[str, Any]] = {}
        direct_statuses: list[str] = []

        for test_label in tree_node.get("tests", []):
            entry = self._build_test_entry(
                test_label, test_set_tests, results_by_name,
            )
            if entry is not None:
                test_entries[test_label] = entry
                if "status" in entry:
                    direct_statuses.append(entry["status"])

        # Recurse into subsets
        subset_nodes: list[dict[str, Any]] = []
        subset_statuses: list[str] = []
        for subset in tree_node.get("subsets", []):
            child = self._build_report_node(
                subset, test_set_tests, results_by_name,
            )
            subset_nodes.append(child)
            subset_statuses.append(child["status"])

        agg_status = _aggregate_status(direct_statuses + subset_statuses)

        node: dict[str, Any] = {
            "name": tree_node.get("name", ""),
            "assertion": tree_node.get("assertion", ""),
            "requirement_id": tree_node.get("requirement_id", ""),
            "status": agg_status,
            "tests": test_entries,
            "subsets": subset_nodes,
        }

        lifecycle_summary = self._compute_lifecycle_summary(
            test_entries, subset_nodes,
        )
        if lifecycle_summary is not None:
            node["lifecycle_summary"] = lifecycle_summary

        return node

    def _build_flat_report_node(
        self,
        test_set_info: dict[str, Any],
        test_set_tests: dict[str, dict[str, Any]],
        results_by_name: dict[str, TestResult],
    ) -> dict[str, Any]:
        """Build a flat report node (backward compat for old manifests)."""
        test_entries: dict[str, dict[str, Any]] = {}
        for name in test_set_tests:
            entry = self._build_test_entry(
                name, test_set_tests, results_by_name,
            )
            if entry is not None:
                test_entries[name] = entry

        statuses = [
            e["status"] for e in test_entries.values() if "status" in e
        ]
        agg_status = _aggregate_status(statuses)

        node: dict[str, Any] = {
            "name": test_set_info.get("name", ""),
            "assertion": test_set_info.get("assertion", ""),
            "requirement_id": test_set_info.get("requirement_id", ""),
            "status": agg_status,
            "tests": test_entries,
            "subsets": [],
        }

        lifecycle_summary = self._compute_lifecycle_summary(
            test_entries, [],
        )
        if lifecycle_summary is not None:
            node["lifecycle_summary"] = lifecycle_summary

        return node

    def _build_test_entry(
        self,
        name: str,
        test_set_tests: dict[str, dict[str, Any]],
        results_by_name: dict[str, TestResult],
    ) -> dict[str, Any] | None:
        """Build a single test entry with metadata and results."""
        data = test_set_tests.get(name)
        if data is None:
            return None

        entry: dict[str, Any] = {
            "assertion": data.get("assertion", ""),
            "requirement_id": data.get("requirement_id", ""),
            "depends_on": list(data.get("depends_on", [])),
        }

        if name in results_by_name:
            result = results_by_name[name]
            entry.update(self._format_result(result))
            entry.pop("name", None)
        else:
            entry["status"] = "not_run"

        if name in self.burn_in_data:
            entry["burn_in"] = self.burn_in_data[name]

        if name in self.inferred_deps:
            entry["inferred_dependencies"] = self.inferred_deps[name]

        if name in self.lifecycle_data:
            entry["lifecycle"] = self.lifecycle_data[name]

        return entry

    def _compute_lifecycle_summary(
        self,
        test_entries: dict[str, dict[str, Any]],
        subset_nodes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Compute lifecycle summary for a test set node.

        Aggregates lifecycle state counts and reliability from direct
        tests and child subsets.

        Returns:
            Summary dict or None if no lifecycle data is available.
        """
        state_counts: dict[str, int] = {
            "new": 0,
            "burning_in": 0,
            "stable": 0,
            "flaky": 0,
            "disabled": 0,
        }
        total = 0
        aggregate_runs = 0
        aggregate_passes = 0

        for _test_name, test_data in test_entries.items():
            lifecycle = test_data.get("lifecycle")
            if lifecycle is None:
                continue
            state = lifecycle.get("state", "new")
            if state in state_counts:
                state_counts[state] += 1
            total += 1
            aggregate_runs += lifecycle.get("runs", 0)
            aggregate_passes += lifecycle.get("passes", 0)

        for subset in subset_nodes:
            child_summary = subset.get("lifecycle_summary")
            if child_summary is None:
                continue
            for state_name in state_counts:
                state_counts[state_name] += child_summary.get(
                    state_name, 0
                )
            total += child_summary.get("total", 0)
            aggregate_runs += child_summary.get("aggregate_runs", 0)
            aggregate_passes += child_summary.get(
                "aggregate_passes", 0
            )

        if total == 0:
            return None

        aggregate_reliability = (
            aggregate_passes / aggregate_runs
            if aggregate_runs > 0
            else 0.0
        )

        return {
            "total": total,
            **state_counts,
            "aggregate_runs": aggregate_runs,
            "aggregate_passes": aggregate_passes,
            "aggregate_reliability": round(aggregate_reliability, 6),
        }

    def _compute_summary(self) -> dict[str, Any]:
        """Compute summary statistics from results.

        Returns:
            Dictionary with counts and total duration.
        """
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "passed")
        failed = sum(1 for r in self.results if r.status == "failed")
        dep_failed = sum(
            1 for r in self.results if r.status == "dependencies_failed"
        )
        passed_dep_failed = sum(
            1
            for r in self.results
            if r.status == "passed+dependencies_failed"
        )
        failed_dep_failed = sum(
            1
            for r in self.results
            if r.status == "failed+dependencies_failed"
        )
        total_duration = sum(r.duration for r in self.results)

        summary: dict[str, Any] = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "dependencies_failed": dep_failed,
            "passed+dependencies_failed": passed_dep_failed,
            "failed+dependencies_failed": failed_dep_failed,
            "total_duration_seconds": round(total_duration, 3),
        }

        if self.manifest:
            all_test_names = set(
                self.manifest.get("test_set_tests", {}).keys()
            )
            executed_names = {r.name for r in self.results}
            not_run = len(all_test_names - executed_names)
            if not_run:
                summary["not_run"] = not_run

        return summary

    def _format_result(self, result: TestResult) -> dict[str, Any]:
        """Format a single test result for the report.

        Args:
            result: TestResult to format.

        Returns:
            Dictionary representing one test entry in the report.
        """
        entry: dict[str, Any] = {
            "name": result.name,
            "assertion": result.assertion,
            "status": result.status,
            "duration_seconds": round(result.duration, 3),
        }

        if result.exit_code is not None:
            entry["exit_code"] = result.exit_code

        if self.commit_hash:
            entry["commit"] = self.commit_hash

        # Include logs only if non-empty
        if result.stdout:
            entry["stdout"] = result.stdout
        if result.stderr:
            entry["stderr"] = result.stderr

        return entry


def _aggregate_status(statuses: list[str]) -> str:
    """Compute aggregated status from child statuses.

    Ignores ``not_run`` entries so that tests absent from the current
    execution do not influence the aggregated pass/fail verdict.

    Args:
        statuses: List of child test statuses.

    Returns:
        Aggregated status string.
    """
    active = [s for s in statuses if s != "not_run"]
    if not active:
        # All children are not_run → propagate not_run (vs truly empty)
        if statuses:
            return "not_run"
        return "no_tests"

    if all(s == "passed" for s in active):
        return "passed"
    # Check for actual test failures (not just dependency failures)
    failure_statuses = {"failed", "failed+dependencies_failed"}
    if any(s in failure_statuses for s in active):
        return "failed"
    return "mixed"
