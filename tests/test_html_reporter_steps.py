"""Tests for step rendering and not_run status in the HTML reporter.

Tests construct StepSegment/BlockSegment objects directly and call
_render_block_segment() to verify the rendered HTML contains expected
elements, attributes, and structure.  Also tests for the ``not_run``
status rendering in test entries, DAG visualization, and the "Show all
workspace tests" toggle checkbox.
"""

from __future__ import annotations

from orchestrator.analysis.log_parser import BlockSegment, StepSegment
from orchestrator.execution.executor import TestResult
from orchestrator.reporting.html_reporter import (
    _build_graph_data,
    _render_block_segment,
    _render_dag_section,
    _render_header,
    _render_test_entry,
    _render_trigger_context,
)
from orchestrator.reporting.reporter import Reporter, _aggregate_status


class TestStepRendering:
    """Tests for step rendering in _render_block_segment."""

    def test_step_renders_as_details(self):
        """Passed step produces <details> without open attribute."""
        step = StepSegment(
            step="s1",
            description="Check inventory",
            status="passed",
        )
        block = BlockSegment(block="stimulation", steps=[step])
        html = _render_block_segment(block)

        assert "<details" in html
        assert "Check inventory" in html
        assert "s1" in html
        # Passed step should NOT have the open attribute
        # Find the step-segment details tag specifically
        idx = html.find('class="step-segment')
        assert idx != -1
        # Extract the details tag that contains step-segment
        details_start = html.rfind("<details", 0, idx)
        details_end = html.find(">", details_start)
        details_tag = html[details_start:details_end + 1]
        assert "open" not in details_tag

    def test_failed_step_expanded(self):
        """Failed step has <details open>."""
        step = StepSegment(
            step="s1",
            description="Failing step",
            status="failed",
        )
        block = BlockSegment(block="stimulation", steps=[step])
        html = _render_block_segment(block)

        # Find the step-segment details tag
        idx = html.find('class="step-segment')
        assert idx != -1
        details_start = html.rfind("<details", 0, idx)
        details_end = html.find(">", details_start)
        details_tag = html[details_start:details_end + 1]
        assert "open" in details_tag

    def test_warning_step_expanded(self):
        """Warning step has <details open>."""
        step = StepSegment(
            step="undefined",
            description="Structural error",
            status="warning",
        )
        block = BlockSegment(block="stimulation", steps=[step])
        html = _render_block_segment(block)

        idx = html.find('class="step-segment')
        assert idx != -1
        details_start = html.rfind("<details", 0, idx)
        details_end = html.find(">", details_start)
        details_tag = html[details_start:details_end + 1]
        assert "open" in details_tag

    def test_ancestor_expansion(self):
        """Failed step 3 levels deep: all ancestors expanded."""
        # Build: outer > middle > inner (failed)
        inner = StepSegment(
            step="inner", description="Inner step", status="failed",
        )
        middle = StepSegment(
            step="middle", description="Middle step",
            status="failed",  # propagated
            steps=[inner],
        )
        outer = StepSegment(
            step="outer", description="Outer step",
            status="failed",  # propagated
            steps=[middle],
        )
        block = BlockSegment(block="stimulation", steps=[outer])
        html = _render_block_segment(block)

        # All three step <details> should have "open"
        # Count occurrences of step-segment with open
        parts = html.split("step-segment")
        open_count = 0
        for part in parts[1:]:  # skip the part before first occurrence
            # Look backwards from the split point for the <details tag
            # The pattern is: <details ... class="step-segment ..." open>
            if "open" in part.split(">")[0]:
                open_count += 1
        assert open_count == 3, (
            f"Expected 3 expanded step-segments, got {open_count}"
        )

    def test_step_header_content(self):
        """Summary contains badge, description, step name."""
        step = StepSegment(
            step="validate_order",
            description="Validate the order",
            status="passed",
        )
        block = BlockSegment(block="checkpoint", steps=[step])
        html = _render_block_segment(block)

        # Status badge
        assert "step-status-badge" in html
        assert "PASSED" in html

        # Description
        assert "Validate the order" in html

        # Step name in muted style
        assert "step-name" in html
        assert "validate_order" in html

    def test_step_body_measurements(self):
        """Measurements table inside step body."""
        step = StepSegment(
            step="measure",
            description="Take measurement",
            status="passed",
            measurements=[
                {"name": "latency", "value": 42, "unit": "ms"},
            ],
        )
        block = BlockSegment(block="stimulation", steps=[step])
        html = _render_block_segment(block)

        assert "measurements-table" in html
        assert "latency" in html
        assert "42" in html
        assert "ms" in html

    def test_step_body_errors(self):
        """Error div inside step body."""
        step = StepSegment(
            step="broken",
            description="Broken step",
            status="failed",
            errors=[{"message": "connection refused"}],
        )
        block = BlockSegment(block="stimulation", steps=[step])
        html = _render_block_segment(block)

        assert "block-error" in html
        assert "connection refused" in html

    def test_nested_step_indentation(self):
        """Sub-steps are rendered inside parent body (DOM nesting)."""
        child = StepSegment(
            step="child",
            description="Child step",
            status="passed",
        )
        parent = StepSegment(
            step="parent",
            description="Parent step",
            status="passed",
            steps=[child],
        )
        block = BlockSegment(block="stimulation", steps=[parent])
        html = _render_block_segment(block)

        # Both step names should appear
        assert "parent" in html
        assert "child" in html

        # Verify nesting: child step-segment appears after parent's
        # opening details tag.  The child should be inside the parent's
        # <details> element.
        parent_idx = html.find("Parent step")
        child_idx = html.find("Child step")
        assert parent_idx < child_idx, (
            "Child step should appear after parent step in HTML"
        )

        # Count step-segment occurrences (should be 2)
        assert html.count("step-segment") >= 2

    def test_block_without_steps(self):
        """Block without steps renders identically (no step elements)."""
        block = BlockSegment(
            block="rigging",
            features=[{"name": "auth"}],
        )
        html = _render_block_segment(block)

        # Should have auth feature
        assert "auth" in html

        # Should NOT have any step-related elements
        assert "step-segment" not in html
        assert "step-header" not in html
        assert "step-status-badge" not in html
        assert "step-name" not in html


class TestUndecidedStatus:
    """Tests for the undecided/missing_result status in reporter and HTML rendering."""

    def test_undecided_status_badge_renders(self):
        """Test entry with status=undecided renders UNDECIDED badge."""
        data = {"status": "undecided", "assertion": "Should pass"}
        result = _render_test_entry("//pkg:my_test", data)
        assert "UNDECIDED" in result

    def test_undecided_dag_node_has_blue_color(self):
        """_build_graph_data produces dag_color=blue for undecided tests."""
        test_set = {
            "name": "root",
            "assertion": "",
            "status": "success",
            "tests": {
                "//pkg:ran": {
                    "status": "success",
                    "depends_on": [],
                },
                "//pkg:skipped": {
                    "status": "undecided",
                    "depends_on": [],
                },
            },
            "subsets": [],
        }
        graph = _build_graph_data(test_set)
        nodes_by_id = {n["data"]["id"]: n["data"] for n in graph["nodes"]}
        assert nodes_by_id["//pkg:skipped"]["dag_color"] == "blue"
        assert nodes_by_id["//pkg:ran"]["dag_color"] == "green"

    def test_show_all_checkbox_in_toolbar(self):
        """_render_dag_section output contains the toggle checkbox."""
        report = {
            "test_set": {
                "name": "root",
                "assertion": "",
                "status": "success",
                "tests": {},
                "subsets": [],
            },
        }
        result = _render_dag_section(report)
        assert 'id="dag-show-all"' in result
        assert "Show all workspace tests" in result

    def test_undecided_summary_badge(self):
        """Header shows Undecided badge when summary has undecided count."""
        report = {
            "summary": {
                "total": 5,
                "success": 3,
                "failed": 0,
                "undecided": 2,
                "total_duration_seconds": 1.0,
            },
        }
        result = _render_header(report)
        assert "Undecided: 2" in result

    def test_build_test_entry_undecided(self):
        """Test in manifest without result gets status=undecided (no execution scope)."""
        reporter = Reporter()
        reporter.set_manifest({
            "test_set": {"name": "root", "tests": ["//a:test"]},
            "test_set_tests": {
                "//a:test": {"assertion": "a works", "depends_on": []},
                "//b:test": {"assertion": "b works", "depends_on": []},
            },
        })
        reporter.add_result(TestResult(
            name="//a:test", assertion="a works",
            status="passed", duration=1.0,
            stdout="", stderr="", exit_code=0,
        ))
        report = reporter.generate_report()
        test_set = report["report"]["test_set"]
        # //a:test ran and passed → success
        assert test_set["tests"]["//a:test"]["status"] == "success"
        # //b:test was not executed, no execution scope → undecided
        assert test_set["tests"]["//b:test"]["status"] == "undecided"

    def test_build_test_entry_missing_result_with_scope(self):
        """Test in execution scope without result gets status=missing_result."""
        reporter = Reporter()
        reporter.set_manifest({
            "test_set": {"name": "root", "tests": ["//a:test"]},
            "test_set_tests": {
                "//a:test": {"assertion": "a works", "depends_on": []},
                "//b:test": {"assertion": "b works", "depends_on": []},
            },
        })
        reporter.set_execution_scope({"//a:test", "//b:test"})
        reporter.add_result(TestResult(
            name="//a:test", assertion="a works",
            status="passed", duration=1.0,
            stdout="", stderr="", exit_code=0,
        ))
        report = reporter.generate_report()
        test_set = report["report"]["test_set"]
        assert test_set["tests"]["//a:test"]["status"] == "success"
        # //b:test in scope but no result → missing_result
        assert test_set["tests"]["//b:test"]["status"] == "missing_result"

    def test_aggregate_status_priority(self):
        """Verdict state aggregation follows priority ordering."""
        assert _aggregate_status(["success", "undecided"]) == "undecided"
        assert _aggregate_status(["undecided", "undecided"]) == "undecided"
        assert _aggregate_status(["failed", "undecided"]) == "undecided"
        assert _aggregate_status(["success", "missing_result"]) == "missing_result"

    def test_summary_includes_undecided_count(self):
        """Summary has undecided count when manifest has unexecuted tests."""
        reporter = Reporter()
        reporter.set_manifest({
            "test_set": {"name": "root"},
            "test_set_tests": {
                "//a:test": {"assertion": "a"},
                "//b:test": {"assertion": "b"},
                "//c:test": {"assertion": "c"},
            },
        })
        reporter.add_result(TestResult(
            name="//a:test", assertion="a",
            status="passed", duration=1.0,
            stdout="", stderr="", exit_code=0,
        ))
        report = reporter.generate_report()
        summary = report["report"]["summary"]
        assert summary["undecided"] == 2


class TestTriggerContext:
    """Tests for the trigger context banner in the report header."""

    def test_ci_gate_shown(self):
        """CI gate name appears when present."""
        report = {"ci_gate_name": "pr_test", "test_set": {"name": "pr_set"}}
        result = _render_trigger_context(report)
        assert "CI Gate:" in result
        assert "pr_test" in result

    def test_test_set_shown_alongside_ci_gate(self):
        """Test set name appears alongside CI gate."""
        report = {"ci_gate_name": "merge_test", "test_set": {"name": "merge_set"}}
        result = _render_trigger_context(report)
        assert "CI Gate:" in result
        assert "merge_test" in result
        assert "Test Set:" in result
        assert "merge_set" in result

    def test_test_set_only_when_no_ci_gate(self):
        """Test set name is the primary label when no CI gate."""
        report = {"test_set": {"name": "my_tests"}}
        result = _render_trigger_context(report)
        assert "CI Gate:" not in result
        assert "Test Set:" in result
        assert "my_tests" in result

    def test_direct_invocation_fallback(self):
        """Shows 'Direct invocation' when no CI gate or test set."""
        report = {"summary": {"total": 1}}
        result = _render_trigger_context(report)
        assert "Direct invocation" in result

    def test_execution_mode_pill(self):
        """Execution mode appears as a pill."""
        report = {"execution_mode": "detection"}
        result = _render_trigger_context(report)
        assert "detection" in result
        assert "trigger-pill" in result

    def test_effort_pill(self):
        """Effort mode appears as a pill."""
        report = {"effort": {"mode": "converge"}}
        result = _render_trigger_context(report)
        assert "effort: converge" in result

    def test_hash_filter_pill(self):
        """Hash filter summary appears as a pill."""
        report = {"hash_filter": {"changed": 5, "unchanged": 95, "skipped": 90}}
        result = _render_trigger_context(report)
        assert "hash-skip: 90/100" in result

    def test_no_effort_no_pill(self):
        """No effort pill when effort is absent."""
        report = {"execution_mode": "diagnostic"}
        result = _render_trigger_context(report)
        assert "effort:" not in result

    def test_trigger_context_in_header(self):
        """_render_header includes trigger context section."""
        report = {
            "ci_gate_name": "pr_test",
            "execution_mode": "detection",
            "summary": {"total": 1, "total_duration_seconds": 0.5},
        }
        result = _render_header(report)
        assert "trigger-context" in result
        assert "CI Gate:" in result
        assert "detection" in result
