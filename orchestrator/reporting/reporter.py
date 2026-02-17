"""Report generation for test execution results.

Generates JSON reports from test results, supporting the five-status model:
passed, failed, dependencies_failed, passed+dependencies_failed,
failed+dependencies_failed.

Supports hierarchical reports mirroring the DAG structure, structured log
data integration, burn-in progress, regression selection details, and
rolling history for reverse-chronological SPRT.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from orchestrator.execution.executor import TestResult


# Valid status values in the five-status model
VALID_STATUSES = frozenset({
    "passed",
    "failed",
    "dependencies_failed",
    "passed+dependencies_failed",
    "failed+dependencies_failed",
})

# Maximum rolling history entries per test
MAX_HISTORY = 500


class Reporter:
    """Collects test results and generates JSON reports.

    The reporter accepts TestResult objects and produces a structured
    JSON report file containing all results with timing, status, and
    log information. Supports hierarchical DAG structure, structured log
    data, burn-in progress, regression selection, and rolling history.
    """

    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self.manifest: dict[str, Any] | None = None
        self.commit_hash: str | None = None
        self.structured_logs: dict[str, dict[str, Any]] = {}
        self.burn_in_data: dict[str, dict[str, Any]] = {}
        self.regression_selection: dict[str, Any] | None = None
        self.inferred_deps: dict[str, list[dict[str, Any]]] = {}
        self.e_value_verdict: dict[str, Any] | None = None

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

    def add_structured_log(
        self, test_name: str, parsed_output: dict[str, Any]
    ) -> None:
        """Add parsed structured log data for a test.

        Args:
            test_name: Test label.
            parsed_output: Output from parse_test_output().
        """
        self.structured_logs[test_name] = parsed_output

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

        if self.manifest:
            report["test_set"] = self._build_hierarchical_report()
        else:
            report["tests"] = [self._format_result(r) for r in self.results]

        if self.regression_selection:
            report["regression_selection"] = self.regression_selection

        if self.e_value_verdict:
            report["e_value_verdict"] = self.e_value_verdict

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
        return report

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

        return {
            "name": tree_node.get("name", ""),
            "assertion": tree_node.get("assertion", ""),
            "requirement_id": tree_node.get("requirement_id", ""),
            "status": agg_status,
            "tests": test_entries,
            "subsets": subset_nodes,
        }

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
            results_by_name[n].status
            for n in test_set_tests
            if n in results_by_name
        ]
        agg_status = _aggregate_status(statuses)

        return {
            "name": test_set_info.get("name", ""),
            "assertion": test_set_info.get("assertion", ""),
            "requirement_id": test_set_info.get("requirement_id", ""),
            "status": agg_status,
            "tests": test_entries,
            "subsets": [],
        }

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
        }

        if name in results_by_name:
            result = results_by_name[name]
            entry.update(self._format_result(result))
            entry.pop("name", None)

        if name in self.structured_logs:
            log_data = self.structured_logs[name]
            entry["structured_log"] = {
                "block_sequence": log_data.get("block_sequence", []),
                "measurements": log_data.get("measurements", []),
                "results": log_data.get("results", []),
                "errors": log_data.get("errors", []),
                "has_rigging_failure": log_data.get(
                    "has_rigging_failure", False
                ),
            }

        if name in self.burn_in_data:
            entry["burn_in"] = self.burn_in_data[name]

        if name in self.inferred_deps:
            entry["inferred_dependencies"] = self.inferred_deps[name]

        return entry

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

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "dependencies_failed": dep_failed,
            "passed+dependencies_failed": passed_dep_failed,
            "failed+dependencies_failed": failed_dep_failed,
            "total_duration_seconds": round(total_duration, 3),
        }

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

    Args:
        statuses: List of child test statuses.

    Returns:
        Aggregated status string.
    """
    if not statuses:
        return "no_tests"

    if all(s == "passed" for s in statuses):
        return "passed"
    # Check for actual test failures (not just dependency failures)
    failure_statuses = {"failed", "failed+dependencies_failed"}
    if any(s in failure_statuses for s in statuses):
        return "failed"
    return "mixed"
