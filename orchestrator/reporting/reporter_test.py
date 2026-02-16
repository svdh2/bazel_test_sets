"""Unit tests for the reporter module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from orchestrator.execution.executor import TestResult
from orchestrator.reporting.reporter import MAX_HISTORY, VALID_STATUSES, Reporter, _aggregate_status


class TestReporterBasics:
    """Tests for basic Reporter functionality."""

    def test_empty_report(self):
        """Empty reporter generates report with zero counts."""
        reporter = Reporter()
        report = reporter.generate_report()

        assert "report" in report
        summary = report["report"]["summary"]
        assert summary["total"] == 0
        assert summary["passed"] == 0
        assert summary["failed"] == 0
        assert report["report"]["tests"] == []

    def test_add_result(self):
        """Single result is added and reflected in report."""
        reporter = Reporter()
        result = TestResult(
            name="test_a",
            assertion="A works",
            status="passed",
            duration=1.5,
            stdout="output",
            stderr="",
            exit_code=0,
        )
        reporter.add_result(result)

        report = reporter.generate_report()
        assert report["report"]["summary"]["total"] == 1
        assert report["report"]["summary"]["passed"] == 1
        assert len(report["report"]["tests"]) == 1

    def test_add_results_bulk(self):
        """Multiple results added via add_results."""
        reporter = Reporter()
        results = [
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
            TestResult(name="b", assertion="B", status="failed", duration=2.0),
        ]
        reporter.add_results(results)

        report = reporter.generate_report()
        assert report["report"]["summary"]["total"] == 2
        assert len(report["report"]["tests"]) == 2


class TestAllFiveStatuses:
    """Tests for the five-status model."""

    def test_all_statuses_representable(self):
        """All five status values are representable in TestResult."""
        statuses = [
            "passed",
            "failed",
            "dependencies_failed",
            "passed+dependencies_failed",
            "failed+dependencies_failed",
        ]
        for s in statuses:
            r = TestResult(name="test", assertion="a", status=s, duration=1.0)
            assert r.status == s

    def test_valid_statuses_constant(self):
        """VALID_STATUSES contains exactly the five statuses."""
        expected = {
            "passed",
            "failed",
            "dependencies_failed",
            "passed+dependencies_failed",
            "failed+dependencies_failed",
        }
        assert VALID_STATUSES == expected

    def test_summary_counts_all_statuses(self):
        """Summary correctly counts all five status types."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
            TestResult(name="b", assertion="B", status="failed", duration=2.0),
            TestResult(
                name="c",
                assertion="C",
                status="dependencies_failed",
                duration=0.0,
            ),
            TestResult(
                name="d",
                assertion="D",
                status="passed+dependencies_failed",
                duration=3.0,
            ),
            TestResult(
                name="e",
                assertion="E",
                status="failed+dependencies_failed",
                duration=4.0,
            ),
        ])

        report = reporter.generate_report()
        summary = report["report"]["summary"]
        assert summary["total"] == 5
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["dependencies_failed"] == 1
        assert summary["passed+dependencies_failed"] == 1
        assert summary["failed+dependencies_failed"] == 1


class TestReportFormatting:
    """Tests for report data formatting."""

    def test_result_fields(self):
        """Test result entry contains expected fields."""
        reporter = Reporter()
        reporter.add_result(
            TestResult(
                name="my_test",
                assertion="My test works",
                status="passed",
                duration=1.234,
                stdout="hello",
                stderr="",
                exit_code=0,
            )
        )

        report = reporter.generate_report()
        test_entry = report["report"]["tests"][0]
        assert test_entry["name"] == "my_test"
        assert test_entry["assertion"] == "My test works"
        assert test_entry["status"] == "passed"
        assert test_entry["duration_seconds"] == 1.234
        assert test_entry["exit_code"] == 0
        assert test_entry["stdout"] == "hello"
        # stderr is empty so should not be present
        assert "stderr" not in test_entry

    def test_empty_stdout_omitted(self):
        """Empty stdout is omitted from the report entry."""
        reporter = Reporter()
        reporter.add_result(
            TestResult(
                name="a",
                assertion="A",
                status="passed",
                duration=1.0,
                stdout="",
                stderr="",
                exit_code=0,
            )
        )

        report = reporter.generate_report()
        test_entry = report["report"]["tests"][0]
        assert "stdout" not in test_entry
        assert "stderr" not in test_entry

    def test_stderr_included_when_nonempty(self):
        """Non-empty stderr is included in the report entry."""
        reporter = Reporter()
        reporter.add_result(
            TestResult(
                name="a",
                assertion="A",
                status="failed",
                duration=1.0,
                stdout="",
                stderr="error occurred",
                exit_code=1,
            )
        )

        report = reporter.generate_report()
        test_entry = report["report"]["tests"][0]
        assert test_entry["stderr"] == "error occurred"

    def test_dependencies_failed_no_exit_code(self):
        """Dependencies-failed result has no exit_code."""
        reporter = Reporter()
        reporter.add_result(
            TestResult(
                name="a",
                assertion="A",
                status="dependencies_failed",
                duration=0.0,
            )
        )

        report = reporter.generate_report()
        test_entry = report["report"]["tests"][0]
        assert "exit_code" not in test_entry

    def test_total_duration_computed(self):
        """Total duration is sum of all test durations."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.5),
            TestResult(name="b", assertion="B", status="passed", duration=2.3),
            TestResult(name="c", assertion="C", status="failed", duration=0.7),
        ])

        report = reporter.generate_report()
        assert report["report"]["summary"]["total_duration_seconds"] == 4.5

    def test_generated_at_present(self):
        """Report includes generated_at timestamp."""
        reporter = Reporter()
        report = reporter.generate_report()
        assert "generated_at" in report["report"]
        # Should be an ISO format string
        assert "T" in report["report"]["generated_at"]

    def test_results_ordering_preserved(self):
        """Results appear in the order they were added."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="c", assertion="C", status="passed", duration=1.0),
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
            TestResult(name="b", assertion="B", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        names = [t["name"] for t in report["report"]["tests"]]
        assert names == ["c", "a", "b"]


class TestYamlOutput:
    """Tests for YAML file output."""

    def test_yaml_output_valid(self):
        """Written YAML file is valid and can be loaded."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(
                name="test_a",
                assertion="A works",
                status="passed",
                duration=1.5,
                stdout="output line",
                stderr="",
                exit_code=0,
            ),
            TestResult(
                name="test_b",
                assertion="B works",
                status="failed",
                duration=0.3,
                stdout="",
                stderr="assertion error",
                exit_code=1,
            ),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.yaml"
            reporter.write_yaml(path)

            assert path.exists()
            loaded = yaml.safe_load(path.read_text())
            assert "report" in loaded
            assert loaded["report"]["summary"]["total"] == 2
            assert len(loaded["report"]["tests"]) == 2

    def test_yaml_output_all_statuses(self):
        """YAML output includes all five status types."""
        reporter = Reporter()
        for status in VALID_STATUSES:
            reporter.add_result(
                TestResult(
                    name=f"test_{status}",
                    assertion=f"Test {status}",
                    status=status,
                    duration=1.0,
                )
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.yaml"
            reporter.write_yaml(path)

            loaded = yaml.safe_load(path.read_text())
            statuses_in_report = {
                t["status"] for t in loaded["report"]["tests"]
            }
            assert statuses_in_report == VALID_STATUSES

    def test_yaml_output_creates_parent_dirs(self):
        """write_yaml creates parent directories if needed."""
        reporter = Reporter()
        reporter.add_result(
            TestResult(name="a", assertion="A", status="passed", duration=1.0)
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "subdir" / "nested" / "report.yaml"
            reporter.write_yaml(path)
            assert path.exists()

    def test_yaml_output_roundtrip(self):
        """YAML output can be loaded and matches generated report."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(
                name="a",
                assertion="A works",
                status="passed",
                duration=1.234,
                stdout="hello world",
                stderr="",
                exit_code=0,
            ),
            TestResult(
                name="b",
                assertion="B works",
                status="dependencies_failed",
                duration=0.0,
            ),
        ])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.yaml"
            reporter.write_yaml(path)

            loaded = yaml.safe_load(path.read_text())

            # Verify structure
            tests = loaded["report"]["tests"]
            assert len(tests) == 2

            assert tests[0]["name"] == "a"
            assert tests[0]["status"] == "passed"
            assert tests[0]["duration_seconds"] == 1.234
            assert tests[0]["stdout"] == "hello world"
            assert tests[0]["exit_code"] == 0

            assert tests[1]["name"] == "b"
            assert tests[1]["status"] == "dependencies_failed"
            assert tests[1]["duration_seconds"] == 0.0


SAMPLE_MANIFEST = {
    "test_set": {
        "name": "payment_tests",
        "assertion": "Payment system works",
        "requirement_id": "REQ-PAY-001",
    },
    "test_set_tests": {
        "auth_test": {
            "assertion": "Auth works",
            "executable": "/bin/auth",
            "depends_on": [],
            "requirement_id": "REQ-AUTH-001",
        },
        "billing_test": {
            "assertion": "Billing works",
            "executable": "/bin/billing",
            "depends_on": ["auth_test"],
            "requirement_id": "REQ-BILL-001",
        },
    },
}


class TestHierarchicalReport:
    """Tests for hierarchical DAG-mirroring report structure."""

    def test_hierarchical_structure(self):
        """Report mirrors DAG structure when manifest is set."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_results([
            TestResult(name="auth_test", assertion="Auth works", status="passed", duration=1.0),
            TestResult(name="billing_test", assertion="Billing works", status="passed", duration=2.0),
        ])

        report = reporter.generate_report()
        test_set = report["report"]["test_set"]
        assert test_set["name"] == "payment_tests"
        assert test_set["assertion"] == "Payment system works"
        assert "auth_test" in test_set["tests"]
        assert "billing_test" in test_set["tests"]

    def test_hierarchical_test_data(self):
        """Hierarchical test entries include assertion and requirement_id."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_results([
            TestResult(name="auth_test", assertion="Auth works", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        auth_entry = report["report"]["test_set"]["tests"]["auth_test"]
        assert auth_entry["assertion"] == "Auth works"
        assert auth_entry["requirement_id"] == "REQ-AUTH-001"
        assert auth_entry["status"] == "passed"

    def test_hierarchical_aggregated_status_passed(self):
        """Aggregated status is 'passed' when all children pass."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_results([
            TestResult(name="auth_test", assertion="Auth works", status="passed", duration=1.0),
            TestResult(name="billing_test", assertion="Billing works", status="passed", duration=2.0),
        ])

        report = reporter.generate_report()
        assert report["report"]["test_set"]["status"] == "passed"

    def test_hierarchical_aggregated_status_failed(self):
        """Aggregated status is 'failed' when any child fails."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_results([
            TestResult(name="auth_test", assertion="Auth works", status="passed", duration=1.0),
            TestResult(name="billing_test", assertion="Billing works", status="failed", duration=2.0),
        ])

        report = reporter.generate_report()
        assert report["report"]["test_set"]["status"] == "failed"

    def test_flat_report_without_manifest(self):
        """Without manifest, report uses flat tests list."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        assert "tests" in report["report"]
        assert "test_set" not in report["report"]


class TestStructuredLogInReport:
    """Tests for structured log data in reports."""

    def test_structured_log_included(self):
        """Structured log data appears in hierarchical report."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_structured_log("auth_test", {
            "block_sequence": ["rigging", "stimulation", "verdict"],
            "measurements": [{"name": "latency", "value": 100, "block": "checkpoint"}],
            "results": [{"status": "pass", "message": "ok", "block": "verdict"}],
            "errors": [],
            "has_rigging_failure": False,
        })
        reporter.add_results([
            TestResult(name="auth_test", assertion="Auth works", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        auth = report["report"]["test_set"]["tests"]["auth_test"]
        assert "structured_log" in auth
        assert auth["structured_log"]["block_sequence"] == ["rigging", "stimulation", "verdict"]
        assert len(auth["structured_log"]["measurements"]) == 1
        assert auth["structured_log"]["has_rigging_failure"] is False


class TestBurnInProgressInReport:
    """Tests for burn-in progress in reports."""

    def test_burn_in_progress_included(self):
        """Burn-in progress data appears in hierarchical report."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_burn_in_progress("auth_test", {
            "runs": 25,
            "passes": 25,
            "sprt_status": "continue",
        })
        reporter.add_results([
            TestResult(name="auth_test", assertion="Auth works", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        auth = report["report"]["test_set"]["tests"]["auth_test"]
        assert "burn_in" in auth
        assert auth["burn_in"]["runs"] == 25
        assert auth["burn_in"]["sprt_status"] == "continue"


class TestRegressionSelectionInReport:
    """Tests for regression selection details in reports."""

    def test_regression_selection_included(self):
        """Regression selection data appears in report."""
        reporter = Reporter()
        reporter.set_regression_selection({
            "changed_files": ["src/auth.py"],
            "tests_selected": 3,
            "total_stable_tests": 50,
            "fallback_used": False,
        })
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        assert "regression_selection" in report["report"]
        assert report["report"]["regression_selection"]["changed_files"] == ["src/auth.py"]

    def test_no_regression_selection_when_not_set(self):
        """Report omits regression_selection when not set."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        assert "regression_selection" not in report["report"]


class TestCommitHash:
    """Tests for commit hash tagging."""

    def test_commit_hash_in_report(self):
        """Commit hash appears in report when set."""
        reporter = Reporter()
        reporter.set_commit_hash("abc123")
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        assert report["report"]["commit"] == "abc123"

    def test_commit_hash_in_test_entries(self):
        """Commit hash appears in individual test entries."""
        reporter = Reporter()
        reporter.set_commit_hash("def456")
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        test_entry = report["report"]["tests"][0]
        assert test_entry["commit"] == "def456"


class TestRollingHistory:
    """Tests for rolling history management."""

    def test_rolling_history_appended(self):
        """Current results are appended to history."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report_with_history()
        assert "history" in report["report"]
        assert "a" in report["report"]["history"]
        assert len(report["report"]["history"]["a"]) == 1

    def test_rolling_history_preserves_existing(self):
        """Existing history is preserved and extended."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write initial report with history
            path = Path(tmpdir) / "report.yaml"
            initial = {
                "report": {
                    "history": {
                        "a": [
                            {"status": "passed", "duration_seconds": 1.0, "timestamp": "t1"},
                            {"status": "failed", "duration_seconds": 2.0, "timestamp": "t2"},
                        ],
                    },
                },
            }
            with open(path, "w") as f:
                yaml.dump(initial, f)

            # Generate new report extending history
            reporter = Reporter()
            reporter.add_results([
                TestResult(name="a", assertion="A", status="passed", duration=0.5),
            ])

            report = reporter.generate_report_with_history(path)
            assert len(report["report"]["history"]["a"]) == 3

    def test_rolling_history_trimmed(self):
        """History is trimmed to MAX_HISTORY entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write initial report with MAX_HISTORY entries
            path = Path(tmpdir) / "report.yaml"
            initial = {
                "report": {
                    "history": {
                        "a": [
                            {"status": "passed", "duration_seconds": 1.0, "timestamp": f"t{i}"}
                            for i in range(MAX_HISTORY)
                        ],
                    },
                },
            }
            with open(path, "w") as f:
                yaml.dump(initial, f)

            # Add one more result
            reporter = Reporter()
            reporter.add_results([
                TestResult(name="a", assertion="A", status="passed", duration=0.5),
            ])

            report = reporter.generate_report_with_history(path)
            assert len(report["report"]["history"]["a"]) == MAX_HISTORY

    def test_rolling_history_no_existing(self):
        """History works without existing report file."""
        reporter = Reporter()
        reporter.add_results([
            TestResult(name="a", assertion="A", status="passed", duration=1.0),
        ])

        report = reporter.generate_report_with_history(None)
        assert len(report["report"]["history"]["a"]) == 1


class TestInferredDependencies:
    """Tests for inferred rigging dependencies in report."""

    def test_inferred_deps_included(self):
        """Inferred dependencies appear in hierarchical report."""
        reporter = Reporter()
        reporter.set_manifest(SAMPLE_MANIFEST)
        reporter.add_inferred_dependencies("billing_test", [
            {"target": "notification_test", "feature": "notifications", "inferred": True},
        ])
        reporter.add_results([
            TestResult(name="billing_test", assertion="Billing works", status="passed", duration=1.0),
        ])

        report = reporter.generate_report()
        billing = report["report"]["test_set"]["tests"]["billing_test"]
        assert "inferred_dependencies" in billing
        assert billing["inferred_dependencies"][0]["target"] == "notification_test"
        assert billing["inferred_dependencies"][0]["inferred"] is True


class TestAggregateStatus:
    """Tests for status aggregation function."""

    def test_all_passed(self):
        assert _aggregate_status(["passed", "passed"]) == "passed"

    def test_any_failed(self):
        assert _aggregate_status(["passed", "failed"]) == "failed"

    def test_empty(self):
        assert _aggregate_status([]) == "no_tests"

    def test_mixed_without_failure(self):
        assert _aggregate_status(["passed", "dependencies_failed"]) == "mixed"

    def test_combined_status_failed(self):
        assert _aggregate_status(["passed", "failed+dependencies_failed"]) == "failed"
