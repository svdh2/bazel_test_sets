"""Tests for step rendering in the HTML reporter.

Tests construct StepSegment/BlockSegment objects directly and call
_render_block_segment() to verify the rendered HTML contains expected
elements, attributes, and structure.
"""

from __future__ import annotations

from orchestrator.analysis.log_parser import BlockSegment, StepSegment
from orchestrator.reporting.html_reporter import _render_block_segment


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
