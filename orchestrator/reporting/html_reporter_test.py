"""Tests for HTML report generation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from orchestrator.reporting.html_reporter import (
    STATUS_COLORS,
    STATUS_LABELS,
    generate_html_from_yaml,
    generate_html_report,
    write_html_report,
)


def _make_flat_report(
    tests: list[dict] | None = None,
    summary: dict | None = None,
) -> dict:
    """Build a minimal flat report dict."""
    if tests is None:
        tests = [
            {
                "name": "test_a",
                "assertion": "A works",
                "status": "passed",
                "duration_seconds": 1.5,
                "exit_code": 0,
            },
        ]
    if summary is None:
        summary = {
            "total": len(tests),
            "passed": sum(1 for t in tests if t.get("status") == "passed"),
            "failed": sum(1 for t in tests if t.get("status") == "failed"),
            "dependencies_failed": 0,
            "total_duration_seconds": sum(
                t.get("duration_seconds", 0) for t in tests
            ),
        }
    return {
        "report": {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "summary": summary,
            "tests": tests,
        }
    }


def _make_hierarchical_report(
    test_set_name: str = "my_tests",
    tests: dict | None = None,
    status: str = "passed",
) -> dict:
    """Build a minimal hierarchical report dict."""
    if tests is None:
        tests = {
            "test_a": {
                "assertion": "A works",
                "requirement_id": "REQ-1",
                "status": "passed",
                "duration_seconds": 1.0,
            },
        }
    return {
        "report": {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "summary": {"total": len(tests), "passed": len(tests), "failed": 0,
                         "dependencies_failed": 0, "total_duration_seconds": 1.0},
            "test_set": {
                "name": test_set_name,
                "assertion": "Suite passes",
                "requirement_id": "REQ-0",
                "status": status,
                "tests": tests,
            },
        }
    }


class TestGenerateHtmlReport:
    """Tests for generate_html_report function."""

    def test_returns_valid_html_structure(self):
        """Output contains DOCTYPE, html, head, body tags."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "<!DOCTYPE html>" in result
        assert "<html" in result
        assert "<head>" in result
        assert "<body>" in result
        assert "</html>" in result

    def test_contains_title(self):
        """Output contains the report title."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "<title>Test Report</title>" in result

    def test_contains_css(self):
        """Output contains inline CSS."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "<style>" in result
        assert "font-family" in result

    def test_contains_generated_at(self):
        """Output shows generation timestamp."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "2026-01-01T00:00:00" in result

    def test_contains_commit_hash(self):
        """Output shows commit hash when present."""
        report = _make_flat_report()
        report["report"]["commit"] = "abc123def"
        result = generate_html_report(report)
        assert "abc123def" in result

    def test_flat_tests_rendered(self):
        """Flat test names appear in output."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "test_a" in result
        assert "A works" in result

    def test_summary_counts_rendered(self):
        """Summary counts appear in output."""
        tests = [
            {"name": "t1", "status": "passed", "duration_seconds": 1.0},
            {"name": "t2", "status": "failed", "duration_seconds": 2.0},
        ]
        summary = {
            "total": 2, "passed": 1, "failed": 1,
            "dependencies_failed": 0,
            "total_duration_seconds": 3.0,
        }
        report = _make_flat_report(tests=tests, summary=summary)
        result = generate_html_report(report)
        assert "Total: 2" in result
        assert "Passed: 1" in result
        assert "Failed: 1" in result

    def test_empty_report(self):
        """Empty report generates valid HTML."""
        report = {"report": {}}
        result = generate_html_report(report)
        assert "<!DOCTYPE html>" in result
        assert "</html>" in result


class TestStatusColors:
    """Tests for color-coded statuses."""

    def test_passed_color_in_output(self):
        """Passed status uses green color."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert STATUS_COLORS["passed"] in result

    def test_failed_color_in_output(self):
        """Failed status uses red color."""
        tests = [{"name": "t", "status": "failed", "duration_seconds": 1.0}]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        assert STATUS_COLORS["failed"] in result

    def test_dependencies_failed_color_in_output(self):
        """Dependencies failed status uses gray color."""
        tests = [{"name": "t", "status": "dependencies_failed",
                  "duration_seconds": 0.5}]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        assert STATUS_COLORS["dependencies_failed"] in result

    def test_all_statuses_have_colors(self):
        """Every status in STATUS_COLORS has a corresponding label."""
        for status in STATUS_COLORS:
            assert status in STATUS_LABELS

    def test_hierarchical_status_colors(self):
        """Hierarchical test set shows correct color for aggregated status."""
        report = _make_hierarchical_report(status="failed")
        result = generate_html_report(report)
        assert STATUS_COLORS["failed"] in result


class TestHierarchicalReport:
    """Tests for hierarchical test set rendering."""

    def test_test_set_name_rendered(self):
        """Test set name appears in output."""
        report = _make_hierarchical_report(test_set_name="checkout_tests")
        result = generate_html_report(report)
        assert "checkout_tests" in result

    def test_test_set_assertion_rendered(self):
        """Test set assertion appears in output."""
        report = _make_hierarchical_report()
        result = generate_html_report(report)
        assert "Suite passes" in result

    def test_nested_tests_rendered(self):
        """Individual tests in hierarchy are rendered."""
        tests = {
            "test_a": {"assertion": "A", "status": "passed",
                       "duration_seconds": 1.0},
            "test_b": {"assertion": "B", "status": "failed",
                       "duration_seconds": 2.0},
        }
        report = _make_hierarchical_report(tests=tests, status="failed")
        result = generate_html_report(report)
        assert "test_a" in result
        assert "test_b" in result

    def test_test_set_status_badge(self):
        """Test set has a status badge."""
        report = _make_hierarchical_report(status="passed")
        result = generate_html_report(report)
        assert "PASSED" in result


class TestExpandableSections:
    """Tests for expandable log and measurement sections."""

    def test_stdout_in_details_element(self):
        """Stdout content appears within a details/summary element."""
        tests = [{
            "name": "t", "status": "passed", "duration_seconds": 1.0,
            "stdout": "Hello from test",
        }]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        assert "<details>" in result
        assert "Hello from test" in result
        assert "Logs" in result

    def test_stderr_rendered(self):
        """Stderr content appears in output."""
        tests = [{
            "name": "t", "status": "failed", "duration_seconds": 1.0,
            "stderr": "Error occurred",
        }]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        assert "Error occurred" in result

    def test_no_logs_no_details(self):
        """No details element when no logs present."""
        tests = [{"name": "t", "status": "passed", "duration_seconds": 1.0}]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        # Should not have a Logs details element
        assert "Logs" not in result

    def test_structured_log_measurements_in_table(self):
        """Measurements from structured log appear in a table."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "structured_log": {
                    "block_sequence": ["rigging", "stimulation"],
                    "measurements": [
                        {"name": "latency", "value": 42, "unit": "ms"},
                    ],
                    "results": [],
                    "errors": [],
                    "has_rigging_failure": False,
                },
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "latency" in result
        assert "42" in result
        assert "ms" in result
        assert "measurements-table" in result

    def test_structured_log_blocks_rendered(self):
        """Block sequence from structured log is rendered."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "structured_log": {
                    "block_sequence": ["rigging", "stimulation", "checkpoint"],
                    "measurements": [],
                    "results": [],
                    "errors": [],
                    "has_rigging_failure": False,
                },
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "rigging" in result
        assert "stimulation" in result
        assert "checkpoint" in result

    def test_structured_log_errors_rendered(self):
        """Errors from structured log are rendered."""
        tests = {
            "t": {
                "assertion": "A", "status": "failed", "duration_seconds": 1.0,
                "structured_log": {
                    "block_sequence": [],
                    "measurements": [],
                    "results": [],
                    "errors": [{"message": "sensor timeout"}],
                    "has_rigging_failure": False,
                },
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "sensor timeout" in result

    def test_rigging_failure_flag_rendered(self):
        """Rigging failure flag is highlighted."""
        tests = {
            "t": {
                "assertion": "A", "status": "failed", "duration_seconds": 1.0,
                "structured_log": {
                    "block_sequence": [],
                    "measurements": [],
                    "results": [],
                    "errors": [],
                    "has_rigging_failure": True,
                },
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "Rigging failure" in result


class TestBurnInSection:
    """Tests for burn-in progress rendering."""

    def test_burn_in_info_rendered(self):
        """Burn-in progress data appears in output."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "burn_in": {"runs": 10, "passes": 10, "sprt_status": "accept"},
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "10 runs" in result
        assert "10 passes" in result
        assert "accept" in result
        assert "burn-in-info" in result


class TestInferredDependencies:
    """Tests for inferred dependencies rendering."""

    def test_inferred_deps_rendered(self):
        """Inferred dependencies appear in expandable section."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "inferred_dependencies": [
                    {"name": "power_supply"},
                    {"name": "sensor_calibration"},
                ],
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "power_supply" in result
        assert "sensor_calibration" in result
        assert "Inferred Dependencies" in result


class TestRegressionSelection:
    """Tests for regression selection section rendering."""

    def test_regression_section_rendered(self):
        """Regression selection section appears when present."""
        report = _make_flat_report()
        report["report"]["regression_selection"] = {
            "changed_files": ["src/auth.py", "src/payment.py"],
            "scores": {"auth_test": 0.85, "payment_test": 0.72},
        }
        result = generate_html_report(report)
        assert "Regression Selection" in result
        assert "src/auth.py" in result
        assert "src/payment.py" in result
        assert "auth_test" in result
        assert "0.85" in result

    def test_no_regression_section_when_absent(self):
        """No regression section when not in report."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "Regression Selection" not in result


class TestWriteHtmlReport:
    """Tests for write_html_report function."""

    def test_writes_file(self):
        """write_html_report creates a file."""
        report = _make_flat_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "report.html"
            write_html_report(report, path)
            assert path.exists()
            content = path.read_text()
            assert "<!DOCTYPE html>" in content

    def test_creates_parent_directories(self):
        """write_html_report creates parent dirs if needed."""
        report = _make_flat_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "dir" / "report.html"
            write_html_report(report, path)
            assert path.exists()


class TestGenerateHtmlFromYaml:
    """Tests for generate_html_from_yaml function."""

    def test_reads_yaml_and_generates_html(self):
        """Can read a YAML file and produce HTML."""
        report = _make_flat_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = Path(tmpdir) / "report.yaml"
            with open(yaml_path, "w") as f:
                yaml.dump(report, f)

            result = generate_html_from_yaml(yaml_path)
            assert "<!DOCTYPE html>" in result
            assert "test_a" in result

    def test_missing_yaml_raises(self):
        """Missing YAML file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            generate_html_from_yaml(Path("/nonexistent/report.yaml"))


class TestHtmlEscaping:
    """Tests for proper HTML escaping."""

    def test_test_name_escaped(self):
        """Test names with special chars are escaped."""
        tests = [{
            "name": "test<script>alert(1)</script>",
            "status": "passed",
            "duration_seconds": 1.0,
        }]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_stdout_escaped(self):
        """Stdout content with HTML chars is escaped."""
        tests = [{
            "name": "t",
            "status": "passed",
            "duration_seconds": 1.0,
            "stdout": '<b>not bold</b> & "quoted"',
        }]
        report = _make_flat_report(tests=tests)
        result = generate_html_report(report)
        assert "&lt;b&gt;" in result
        assert "&amp;" in result
