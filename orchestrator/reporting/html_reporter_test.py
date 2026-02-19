"""Tests for HTML report generation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orchestrator.reporting.html_reporter import (
    LIFECYCLE_COLORS,
    LIFECYCLE_LABELS,
    STATUS_COLORS,
    STATUS_LABELS,
    generate_html_from_file,
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


class TestNestedTestSets:
    """Tests for nested test set data in DAG hidden elements."""

    def _make_nested_report(self, child_status="passed", root_status=None):
        if root_status is None:
            root_status = child_status
        return {
            "report": {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "summary": {"total": 2, "passed": 2, "failed": 0,
                             "dependencies_failed": 0, "total_duration_seconds": 3.0},
                "test_set": {
                    "name": "root_set",
                    "assertion": "Root passes",
                    "status": root_status,
                    "tests": {"test_a": {"status": "passed", "duration_seconds": 1.0}},
                    "subsets": [
                        {
                            "name": "child_set",
                            "assertion": "Child passes",
                            "status": child_status,
                            "tests": {"test_b": {"status": child_status,
                                                  "duration_seconds": 2.0}},
                            "subsets": [],
                        },
                    ],
                },
            },
        }

    def test_nested_set_data_present(self):
        """Nested test set data is present in hidden DAG data elements."""
        result = generate_html_report(self._make_nested_report())
        assert "child_set" in result
        assert 'data-set-name="child_set"' in result

    def test_nested_tests_rendered(self):
        """Tests inside nested subsets are rendered in hidden data elements."""
        result = generate_html_report(self._make_nested_report())
        assert "test_a" in result
        assert "test_b" in result
        assert 'data-test-name="test_a"' in result
        assert 'data-test-name="test_b"' in result


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
        assert "<details" in result
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

    def test_structured_stdout_measurements_in_table(self):
        """Measurements from [TST] stdout appear in a table."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "stimulation"}\n'
                    '[TST] {"type": "measurement", "name": "latency", "value": 42, "unit": "ms"}\n'
                    '[TST] {"type": "block_end", "block": "stimulation"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "latency" in result
        assert "42" in result
        assert "ms" in result
        assert "measurements-table" in result

    def test_structured_stdout_blocks_rendered(self):
        """Block types from [TST] stdout are rendered as block segments."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "rigging"}\n'
                    '[TST] {"type": "block_end", "block": "rigging"}\n'
                    '[TST] {"type": "block_start", "block": "stimulation"}\n'
                    '[TST] {"type": "block_end", "block": "stimulation"}\n'
                    '[TST] {"type": "block_start", "block": "checkpoint"}\n'
                    '[TST] {"type": "block_end", "block": "checkpoint"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "block-rigging" in result
        assert "block-stimulation" in result
        assert "block-checkpoint" in result

    def test_structured_stdout_errors_rendered(self):
        """Errors from [TST] stdout are rendered."""
        tests = {
            "t": {
                "assertion": "A", "status": "failed", "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "rigging"}\n'
                    '[TST] {"type": "error", "message": "sensor timeout"}\n'
                    '[TST] {"type": "block_end", "block": "rigging"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "sensor timeout" in result

    def test_structured_stdout_assertions_rendered(self):
        """Assertions from [TST] result events are rendered."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "verdict"}\n'
                    '[TST] {"type": "result", "name": "discount_applied", "passed": true}\n'
                    '[TST] {"type": "block_end", "block": "verdict"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "discount_applied" in result
        assert "assertion-pass" in result

    def test_plain_stdout_renders_as_raw_pre(self):
        """Stdout with no [TST] lines renders as a simple <pre> block."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "stdout": "Hello world\nTest running...",
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "Hello world" in result
        assert "<pre>" in result
        # Should not contain block-segment divs (CSS class defs are ok)
        assert 'class="block-segment' not in result

    def test_structured_stdout_with_description(self):
        """Block description from block_start is rendered."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed", "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "stimulation", '
                    '"description": "Apply 15% discount"}\n'
                    '[TST] {"type": "block_end", "block": "stimulation"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "Apply 15% discount" in result


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


class TestGenerateHtmlFromFile:
    """Tests for generate_html_from_file function."""

    def test_reads_json_and_generates_html(self):
        """Can read a JSON file and produce HTML."""
        report = _make_flat_report()
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "report.json"
            with open(json_path, "w") as f:
                json.dump(report, f)

            result = generate_html_from_file(json_path)
            assert "<!DOCTYPE html>" in result
            assert "test_a" in result

    def test_missing_file_raises(self):
        """Missing report file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            generate_html_from_file(Path("/nonexistent/report.json"))


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


class TestHistoryTimeline:
    """Tests for pass/fail history timeline rendering."""

    def _make_history_entries(
        self,
        statuses: list[str],
        commits: list[str] | None = None,
    ) -> list[dict]:
        entries = []
        for i, status in enumerate(statuses):
            entry: dict = {"status": status, "duration_seconds": 1.0,
                           "timestamp": f"2026-01-0{i + 1}T00:00:00+00:00"}
            if commits and i < len(commits):
                entry["commit"] = commits[i]
            entries.append(entry)
        return entries

    def test_timeline_rendered_for_flat_report_with_history(self):
        """History timeline appears when history data is present."""
        report = _make_flat_report()
        report["report"]["history"] = {
            "test_a": self._make_history_entries(
                ["passed", "failed", "passed"],
                ["aaa111", "bbb222", "ccc333"],
            ),
        }
        result = generate_html_report(report)
        assert "history-timeline" in result
        assert "ht-box" in result

    def test_timeline_not_rendered_without_history(self):
        """No timeline div when history data is absent."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert 'class="history-timeline"' not in result

    def test_timeline_shows_correct_colors(self):
        """Passed entries are green, failed entries are red."""
        report = _make_flat_report()
        report["report"]["history"] = {
            "test_a": self._make_history_entries(["passed", "failed"]),
        }
        result = generate_html_report(report)
        assert "#2da44e" in result  # passed green
        assert "#cf222e" in result  # failed red

    def test_timeline_shows_commit_in_tooltip(self):
        """Commit hash appears in title attribute for hover."""
        report = _make_flat_report()
        report["report"]["history"] = {
            "test_a": self._make_history_entries(
                ["passed"], ["abcdef123456789"],
            ),
        }
        result = generate_html_report(report)
        assert 'title="abcdef123456"' in result  # truncated to 12 chars

    def test_timeline_falls_back_to_status_when_no_commit(self):
        """Tooltip shows status name when commit is missing."""
        report = _make_flat_report()
        report["report"]["history"] = {
            "test_a": self._make_history_entries(["passed"]),
        }
        result = generate_html_report(report)
        assert 'title="passed"' in result

    def test_timeline_rendered_in_hierarchical_report(self):
        """History timeline appears in hierarchical test sets."""
        report = _make_hierarchical_report()
        report["report"]["history"] = {
            "test_a": self._make_history_entries(
                ["passed", "passed", "failed"],
                ["aaa", "bbb", "ccc"],
            ),
        }
        result = generate_html_report(report)
        assert "history-timeline" in result

    def test_timeline_escapes_html_in_commit(self):
        """Commit hash with special chars is escaped."""
        report = _make_flat_report()
        report["report"]["history"] = {
            "test_a": self._make_history_entries(
                ["passed"], ['<script>"x</script>'],
            ),
        }
        result = generate_html_report(report)
        assert "<script>" not in result
        assert "&lt;script&gt;&quot;" in result

    def test_empty_history_list_no_timeline(self):
        """Empty history list for a test produces no timeline div."""
        report = _make_flat_report()
        report["report"]["history"] = {"test_a": []}
        result = generate_html_report(report)
        assert 'class="history-timeline"' not in result

    def test_dependencies_failed_color(self):
        """Dependencies_failed status uses grey in timeline."""
        report = _make_flat_report(
            tests=[{"name": "t", "status": "dependencies_failed",
                    "duration_seconds": 1.0}],
        )
        report["report"]["history"] = {
            "t": self._make_history_entries(["dependencies_failed"]),
        }
        result = generate_html_report(report)
        assert "#999" in result


class TestLifecycleRendering:
    """Tests for lifecycle state rendering in HTML reports."""

    def test_lifecycle_badge_rendered_for_test(self):
        """Lifecycle state badge appears on individual test entries."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed",
                "duration_seconds": 1.0,
                "lifecycle": {
                    "state": "stable", "runs": 100, "passes": 99,
                    "reliability": 0.99,
                },
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert 'class="lifecycle-badge"' in result
        assert "STABLE" in result
        assert "99.0%" in result
        assert "(99/100)" in result

    def test_no_lifecycle_badge_without_data(self):
        """No lifecycle badge element when lifecycle data is absent."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed",
                "duration_seconds": 1.0,
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        # CSS class definition exists but no rendered badge element
        assert 'class="lifecycle-badge"' not in result

    def test_lifecycle_summary_rendered_for_test_set(self):
        """Lifecycle summary appears on test set headers."""
        report = _make_hierarchical_report()
        report["report"]["test_set"]["lifecycle_summary"] = {
            "total": 3, "stable": 2, "burning_in": 1, "flaky": 0,
            "new": 0, "disabled": 0,
            "aggregate_runs": 300, "aggregate_passes": 297,
            "aggregate_reliability": 0.99,
        }
        result = generate_html_report(report)
        assert "lifecycle-summary" in result
        assert "2 STABLE" in result
        assert "1 BURNING IN" in result
        assert "99.0%" in result

    def test_lifecycle_summary_omits_zero_counts(self):
        """Zero-count states are not shown in summary."""
        report = _make_hierarchical_report()
        report["report"]["test_set"]["lifecycle_summary"] = {
            "total": 2, "stable": 2, "burning_in": 0, "flaky": 0,
            "new": 0, "disabled": 0,
            "aggregate_runs": 200, "aggregate_passes": 200,
            "aggregate_reliability": 1.0,
        }
        result = generate_html_report(report)
        # Zero-count states should not appear as rendered summary items
        assert "0 FLAKY" not in result
        assert "0 BURNING IN" not in result
        assert "2 STABLE" in result

    def test_lifecycle_config_note_rendered(self):
        """Lifecycle config threshold note appears when config is set."""
        report = _make_hierarchical_report()
        report["report"]["test_set"]["lifecycle_summary"] = {
            "total": 1, "stable": 1, "burning_in": 0, "flaky": 0,
            "new": 0, "disabled": 0,
            "aggregate_runs": 100, "aggregate_passes": 100,
            "aggregate_reliability": 1.0,
        }
        report["report"]["lifecycle_config"] = {
            "min_reliability": 0.99,
            "statistical_significance": 0.95,
        }
        result = generate_html_report(report)
        assert "lifecycle-config-note" in result
        assert "99%" in result
        assert "95%" in result

    def test_lifecycle_badge_colors(self):
        """Different lifecycle states use correct colors."""
        for state, expected_color in [
            ("stable", LIFECYCLE_COLORS["stable"]),
            ("flaky", LIFECYCLE_COLORS["flaky"]),
            ("burning_in", LIFECYCLE_COLORS["burning_in"]),
            ("new", LIFECYCLE_COLORS["new"]),
            ("disabled", LIFECYCLE_COLORS["disabled"]),
        ]:
            tests = {
                "t": {
                    "assertion": "A", "status": "passed",
                    "duration_seconds": 1.0,
                    "lifecycle": {
                        "state": state, "runs": 10, "passes": 9,
                        "reliability": 0.9,
                    },
                },
            }
            report = _make_hierarchical_report(tests=tests)
            result = generate_html_report(report)
            assert expected_color in result, (
                f"Expected color {expected_color} for state {state}"
            )

    def test_lifecycle_zero_runs_no_percentage(self):
        """Tests with zero runs show badge but no percentage."""
        tests = {
            "t": {
                "assertion": "A", "status": "passed",
                "duration_seconds": 1.0,
                "lifecycle": {
                    "state": "new", "runs": 0, "passes": 0,
                    "reliability": 0.0,
                },
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert 'class="lifecycle-badge"' in result
        assert "NEW" in result
        # No rendered reliability element (only CSS definition)
        assert 'class="lifecycle-reliability"' not in result

    def test_all_lifecycle_labels_have_colors(self):
        """Every lifecycle state in LIFECYCLE_LABELS has a color."""
        for state in LIFECYCLE_LABELS:
            assert state in LIFECYCLE_COLORS


class TestSourceLinksRendering:
    """Tests for source code link rendering in HTML reports."""

    def _make_report_with_source_links(self, source_link_base=None):
        """Build a report with structured stdout containing _file/_line."""
        tests = {
            "t": {
                "assertion": "A",
                "status": "passed",
                "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "rigging"}\n'
                    '[TST] {"type": "feature", "name": "auth", '
                    '"_file": "examples/test.py", "_line": 14}\n'
                    '[TST] {"type": "block_end", "block": "rigging"}\n'
                    '[TST] {"type": "block_start", "block": "stimulation"}\n'
                    '[TST] {"type": "measurement", "name": "latency", '
                    '"value": 42, "unit": "ms", '
                    '"_file": "examples/test.py", "_line": 18}\n'
                    '[TST] {"type": "block_end", "block": "stimulation"}\n'
                    '[TST] {"type": "block_start", "block": "verdict"}\n'
                    '[TST] {"type": "result", "name": "ok", "passed": true, '
                    '"_file": "examples/test.py", "_line": 22}\n'
                    '[TST] {"type": "block_end", "block": "verdict"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        if source_link_base is not None:
            report["report"]["source_link_base"] = source_link_base
        return report

    def test_github_source_links_rendered(self):
        """Source links render as <a> tags when source_link_base is set."""
        base = "https://github.com/owner/repo/blob/abc123"
        report = self._make_report_with_source_links(source_link_base=base)
        result = generate_html_report(report)
        assert 'class="source-link"' in result
        assert 'target="_blank"' in result
        assert f"{base}/examples/test.py#L14" in result
        assert f"{base}/examples/test.py#L18" in result
        assert f"{base}/examples/test.py#L22" in result

    def test_local_source_links_rendered(self):
        """Source links render as <span> when source_link_base is absent."""
        report = self._make_report_with_source_links()
        result = generate_html_report(report)
        assert '<span class="source-link">' in result
        assert "examples/test.py:14" in result
        assert "examples/test.py:18" in result
        assert "examples/test.py:22" in result

    def test_no_source_links_without_metadata(self):
        """No rendered source link elements when events lack _file/_line."""
        tests = {
            "t": {
                "assertion": "A",
                "status": "passed",
                "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "verdict"}\n'
                    '[TST] {"type": "result", "name": "ok", "passed": true}\n'
                    '[TST] {"type": "block_end", "block": "verdict"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        # CSS class definition is always present, but no rendered elements
        assert 'class="source-link"' not in result

    def test_source_link_css_present(self):
        """Source link CSS class is defined in the stylesheet."""
        report = _make_hierarchical_report()
        result = generate_html_report(report)
        assert ".source-link" in result

    def test_error_source_links_rendered(self):
        """Error events with _file/_line also get source links."""
        tests = {
            "t": {
                "assertion": "A",
                "status": "failed",
                "duration_seconds": 1.0,
                "stdout": (
                    '[TST] {"type": "block_start", "block": "rigging"}\n'
                    '[TST] {"type": "error", "message": "db down", '
                    '"_file": "examples/test.py", "_line": 7}\n'
                    '[TST] {"type": "block_end", "block": "rigging"}'
                ),
            },
        }
        report = _make_hierarchical_report(tests=tests)
        result = generate_html_report(report)
        assert "examples/test.py:7" in result

    def test_measurements_table_has_source_column(self):
        """Measurements table includes a Source column header."""
        base = "https://github.com/owner/repo/blob/abc123"
        report = self._make_report_with_source_links(source_link_base=base)
        result = generate_html_report(report)
        assert "<th>Source</th>" in result


# ---------------------------------------------------------------------------
# DAG Visualization
# ---------------------------------------------------------------------------

def _make_dag_report() -> dict:
    """Build a report with depends_on edges for DAG visualization tests."""
    return {
        "report": {
            "generated_at": "2026-01-01T00:00:00+00:00",
            "summary": {
                "total": 3, "passed": 2, "failed": 1,
                "dependencies_failed": 0, "total_duration_seconds": 3.0,
            },
            "test_set": {
                "name": "root_set",
                "assertion": "Root assertion",
                "requirement_id": "ROOT",
                "status": "failed",
                "tests": {
                    "test_a": {
                        "assertion": "A works",
                        "status": "passed",
                        "duration_seconds": 1.0,
                        "depends_on": [],
                    },
                    "test_b": {
                        "assertion": "B works",
                        "status": "passed",
                        "duration_seconds": 1.0,
                        "depends_on": ["test_a"],
                    },
                },
                "subsets": [
                    {
                        "name": "child_set",
                        "assertion": "Child assertion",
                        "status": "failed",
                        "tests": {
                            "test_c": {
                                "assertion": "C works",
                                "status": "failed",
                                "duration_seconds": 1.0,
                                "depends_on": ["test_a"],
                            },
                        },
                        "subsets": [],
                    },
                ],
            },
        },
    }


class TestDagVisualization:
    """Tests for the interactive DAG visualization section."""

    def test_dag_section_present_for_hierarchical_report(self):
        """DAG section appears when report has a test_set."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert 'class="dag-section"' in result
        assert 'id="dag-canvas"' in result

    def test_dag_section_absent_for_flat_report(self):
        """DAG section does not appear for flat (non-hierarchical) reports."""
        report = _make_flat_report()
        result = generate_html_report(report)
        assert "dag-section" not in result

    def test_graph_data_embedded(self):
        """GRAPH_DATA JavaScript variable is embedded."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert "var GRAPH_DATA=" in result

    def test_cytoscape_cdn_included(self):
        """Cytoscape.js CDN script tag is present."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert "cytoscape.min.js" in result
        assert "dagre.min.js" in result
        assert "cytoscape-dagre.js" in result

    def test_nodes_include_test_names(self):
        """Graph data contains nodes for all tests and groups."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert '"test_a"' in result
        assert '"test_b"' in result
        assert '"test_c"' in result
        assert '"root_set"' in result
        assert '"child_set"' in result

    def test_edges_reflect_depends_on(self):
        """Graph data contains dependency edges for depends_on relationships."""
        report = _make_dag_report()
        result = generate_html_report(report)
        # test_b depends on test_a (dependency edge)
        assert '"source":"test_b","target":"test_a","type":"dependency"' in result
        # test_c also depends on test_a
        assert '"source":"test_c","target":"test_a","type":"dependency"' in result

    def test_edges_reflect_membership(self):
        """Graph data contains membership edges from sets to their members."""
        report = _make_dag_report()
        result = generate_html_report(report)
        # root_set contains test_a and test_b
        assert '"source":"root_set","target":"test_a","type":"member"' in result
        assert '"source":"root_set","target":"test_b","type":"member"' in result
        # root_set contains child_set
        assert '"source":"root_set","target":"child_set","type":"member"' in result
        # child_set contains test_c
        assert '"source":"child_set","target":"test_c","type":"member"' in result

    def test_toolbar_buttons_present(self):
        """Toolbar with zoom and fit buttons is present."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert 'id="dag-zoom-in"' in result
        assert 'id="dag-zoom-out"' in result
        assert 'id="dag-fit"' in result

    def test_detail_pane_present(self):
        """Detail pane with content div is present."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert 'id="dag-detail"' in result
        assert 'id="dag-detail-content"' in result

    def test_handles_empty_depends_on(self):
        """DAG section renders correctly when no test has dependency edges."""
        report = _make_hierarchical_report()
        result = generate_html_report(report)
        assert 'class="dag-section"' in result
        # Only membership edges, no dependency edges
        assert '"type":"dependency"' not in result
        assert '"type":"member"' in result

    def test_detail_pane_clones_rendered_test_entry(self):
        """DAG detail JS finds test entries by data-test-name attribute."""
        report = _make_dag_report()
        result = generate_html_report(report)
        # Test entries have data-test-name attributes for JS to find
        assert 'data-test-name="test_a"' in result
        assert 'data-test-name="test_b"' in result
        assert 'data-test-name="test_c"' in result
        # JS uses querySelectorAll with data-test-name
        assert "data-test-name" in result
        assert "dag-detail-content" in result

    def test_label_shortening_for_bazel_labels(self):
        """Bazel-style labels are shortened to the target name."""
        report = {
            "report": {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "summary": {"total": 1, "passed": 1, "failed": 0,
                             "dependencies_failed": 0,
                             "total_duration_seconds": 1.0},
                "test_set": {
                    "name": "suite",
                    "assertion": "Suite",
                    "status": "passed",
                    "tests": {
                        "@@//pkg:my_test": {
                            "assertion": "Works",
                            "status": "passed",
                            "duration_seconds": 1.0,
                            "depends_on": [],
                        },
                    },
                    "subsets": [],
                },
            },
        }
        result = generate_html_report(report)
        # The short label "my_test" should appear in the graph data
        assert '"label":"my_test"' in result

    def test_lifecycle_state_in_graph_data(self):
        """Graph data includes lifecycle state for test nodes."""
        report = {
            "report": {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "summary": {"total": 1, "passed": 1, "failed": 0,
                             "dependencies_failed": 0,
                             "total_duration_seconds": 1.0},
                "test_set": {
                    "name": "suite",
                    "assertion": "Suite",
                    "status": "passed",
                    "tests": {
                        "test_a": {
                            "assertion": "Works",
                            "status": "passed",
                            "duration_seconds": 1.0,
                            "depends_on": [],
                            "lifecycle": {
                                "state": "flaky", "runs": 10,
                                "passes": 7, "reliability": 0.7,
                            },
                        },
                    },
                    "subsets": [],
                },
            },
        }
        result = generate_html_report(report)
        assert '"lifecycle":"flaky"' in result

    def test_lifecycle_state_empty_when_absent(self):
        """Graph data has empty lifecycle when test has no lifecycle."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert '"lifecycle":""' in result

    def test_group_node_double_border_style(self):
        """Group nodes use double border style with black color."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert "'border-style': 'double'" in result
        assert "'border-color': '#333'" in result

    def test_lifecycle_icons_in_js(self):
        """Lifecycle icon mapping is present in the JS."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert "LIFECYCLE_ICONS" in result

    def test_dag_color_all_passed(self):
        """Set node is green when all children passed."""
        report = _make_hierarchical_report(status="passed")
        result = generate_html_report(report)
        # The single test is passed → green; the set aggregates → green
        assert '"dag_color":"green"' in result

    def test_dag_color_any_failed_propagates_red(self):
        """Set node is red when any child is red."""
        report = _make_dag_report()
        result = generate_html_report(report)
        # test_c failed → child_set red → root_set red
        # Check that root_set and child_set both get red
        import json as _json
        graph_json = result.split("var GRAPH_DATA=")[1].split(";</script>")[0]
        graph = _json.loads(graph_json)
        colors = {n["data"]["id"]: n["data"]["dag_color"]
                  for n in graph["nodes"]}
        assert colors["test_a"] == "green"
        assert colors["test_b"] == "green"
        assert colors["test_c"] == "red"
        assert colors["child_set"] == "red"
        assert colors["root_set"] == "red"

    def test_dag_color_all_grey(self):
        """Set node is grey when all children are grey."""
        report = {
            "report": {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "summary": {"total": 1, "passed": 0, "failed": 0,
                             "dependencies_failed": 1,
                             "total_duration_seconds": 0},
                "test_set": {
                    "name": "suite",
                    "assertion": "Suite",
                    "status": "dependencies_failed",
                    "tests": {
                        "test_x": {
                            "assertion": "X",
                            "status": "dependencies_failed",
                            "duration_seconds": 0,
                            "depends_on": [],
                        },
                    },
                    "subsets": [],
                },
            },
        }
        result = generate_html_report(report)
        import json as _json
        graph_json = result.split("var GRAPH_DATA=")[1].split(";</script>")[0]
        graph = _json.loads(graph_json)
        colors = {n["data"]["id"]: n["data"]["dag_color"]
                  for n in graph["nodes"]}
        assert colors["test_x"] == "grey"
        assert colors["suite"] == "grey"

    def test_dag_color_mixed_green_grey_is_green(self):
        """Set node is green when children are a mix of green and grey."""
        report = {
            "report": {
                "generated_at": "2026-01-01T00:00:00+00:00",
                "summary": {"total": 2, "passed": 1, "failed": 0,
                             "dependencies_failed": 1,
                             "total_duration_seconds": 1.0},
                "test_set": {
                    "name": "suite",
                    "assertion": "Suite",
                    "status": "passed+dependencies_failed",
                    "tests": {
                        "test_ok": {
                            "assertion": "OK",
                            "status": "passed",
                            "duration_seconds": 1.0,
                            "depends_on": [],
                        },
                        "test_skip": {
                            "assertion": "Skip",
                            "status": "dependencies_failed",
                            "duration_seconds": 0,
                            "depends_on": [],
                        },
                    },
                    "subsets": [],
                },
            },
        }
        result = generate_html_report(report)
        import json as _json
        graph_json = result.split("var GRAPH_DATA=")[1].split(";</script>")[0]
        graph = _json.loads(graph_json)
        colors = {n["data"]["id"]: n["data"]["dag_color"]
                  for n in graph["nodes"]}
        assert colors["test_ok"] == "green"
        assert colors["test_skip"] == "grey"
        assert colors["suite"] == "green"

    def test_set_summary_cards_have_data_set_name(self):
        """Hidden set summary cards carry data-set-name for each set."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert 'data-set-name="root_set"' in result
        assert 'data-set-name="child_set"' in result

    def test_group_node_click_handler_in_js(self):
        """JS includes a tap handler for node.group to populate detail pane."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert "node.group" in result
        assert "data-set-name" in result

    def test_set_summary_card_contains_assertion(self):
        """Hidden set summary card includes the set's assertion text."""
        report = _make_dag_report()
        result = generate_html_report(report)
        # Extract the hidden card for root_set
        marker = 'data-set-name="root_set"'
        idx = result.index(marker)
        # The card's closing </div> is within a reasonable distance
        card_snippet = result[idx:idx + 500]
        assert "Root assertion" in card_snippet

    def test_set_summary_card_hidden_by_default(self):
        """Hidden set summary cards use display:none."""
        report = _make_dag_report()
        result = generate_html_report(report)
        assert 'data-set-name="root_set" style="display:none"' in result

    def test_set_summary_card_includes_lifecycle_summary(self):
        """Hidden set summary card includes lifecycle summary when present."""
        report = _make_dag_report()
        report["report"]["test_set"]["lifecycle_summary"] = {
            "total": 3, "stable": 2, "flaky": 1, "burning_in": 0,
            "new": 0, "disabled": 0,
            "aggregate_runs": 300, "aggregate_passes": 295,
            "aggregate_reliability": 0.983,
        }
        result = generate_html_report(report)
        marker = 'data-set-name="root_set"'
        idx = result.index(marker)
        card_snippet = result[idx:idx + 1000]
        assert "2 STABLE" in card_snippet
        assert "1 FLAKY" in card_snippet
        assert "98.3%" in card_snippet

    def test_set_summary_card_includes_history_timeline(self):
        """Hidden set summary card includes aggregated history timeline."""
        report = _make_dag_report()
        report["report"]["history"] = {
            "test_a": [
                {"status": "passed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T00:00:00+00:00"},
                {"status": "passed", "commit": "bbb", "duration_seconds": 1.0,
                 "timestamp": "2026-01-02T00:00:00+00:00"},
            ],
            "test_b": [
                {"status": "passed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T00:00:00+00:00"},
                {"status": "failed", "commit": "bbb", "duration_seconds": 1.0,
                 "timestamp": "2026-01-02T00:00:00+00:00"},
            ],
            "test_c": [
                {"status": "passed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T00:00:00+00:00"},
                {"status": "failed", "commit": "bbb", "duration_seconds": 1.0,
                 "timestamp": "2026-01-02T00:00:00+00:00"},
            ],
        }
        result = generate_html_report(report)
        # root_set contains test_a, test_b and child_set (which has test_c)
        marker = 'data-set-name="root_set"'
        idx = result.index(marker)
        card_snippet = result[idx:idx + 2000]
        # Should have a history timeline inside the card
        assert "history-timeline" in card_snippet
        # commit aaa: all passed → green (#2da44e)
        assert "#2da44e" in card_snippet
        # commit bbb: test_b failed → red (#cf222e)
        assert "#cf222e" in card_snippet

    def test_set_summary_card_no_history_without_data(self):
        """Set summary card has no timeline when no history is present."""
        report = _make_dag_report()
        result = generate_html_report(report)
        marker = 'data-set-name="root_set"'
        idx = result.index(marker)
        card_snippet = result[idx:idx + 1000]
        assert "history-timeline" not in card_snippet

    def test_set_history_aggregates_child_set_tests(self):
        """Set history includes tests from nested child sets."""
        report = _make_dag_report()
        # Only test_c (in child_set) has history, root_set should still
        # show a timeline since test_c is a descendant
        report["report"]["history"] = {
            "test_c": [
                {"status": "failed", "commit": "xxx", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T00:00:00+00:00"},
            ],
        }
        result = generate_html_report(report)
        marker = 'data-set-name="root_set"'
        idx = result.index(marker)
        card_snippet = result[idx:idx + 2000]
        assert "history-timeline" in card_snippet
        # test_c failed → root_set should show red
        assert "#cf222e" in card_snippet

    def test_set_history_multiple_runs_same_commit(self):
        """Multiple runs at the same commit produce separate timeline boxes."""
        report = _make_dag_report()
        report["report"]["history"] = {
            "test_a": [
                {"status": "passed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T00:00:00+00:00"},
                {"status": "passed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T01:00:00+00:00"},
            ],
            "test_b": [
                {"status": "passed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T00:00:00+00:00"},
                {"status": "failed", "commit": "aaa", "duration_seconds": 1.0,
                 "timestamp": "2026-01-01T01:00:00+00:00"},
            ],
        }
        result = generate_html_report(report)
        marker = 'data-set-name="root_set"'
        idx = result.index(marker)
        # Narrow to just the hidden card (ends at first closing </div> sequence)
        end_marker = "history-timeline"
        timeline_idx = result.index(end_marker, idx)
        # Grab enough to cover the timeline div but not the test entries
        card_snippet = result[timeline_idx:timeline_idx + 300]
        # Should have two ht-box elements (one per run), not one
        assert card_snippet.count("ht-box") == 2
        # Run 1: all passed → green; Run 2: test_b failed → red
        assert "#2da44e" in card_snippet
        assert "#cf222e" in card_snippet
