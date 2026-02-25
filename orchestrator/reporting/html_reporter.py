"""HTML report generation from JSON test reports.

Generates self-contained HTML reports with color-coded statuses,
expandable log and measurement sections, and visual DAG structure.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from orchestrator.analysis.log_parser import (
    BlockSegment,
    StepSegment,
    TextSegment,
    parse_stdout_segments,
)
from orchestrator.reporting.source_links import render_source_link


# Status color mapping (verdict states + backward-compat aliases)
STATUS_COLORS: dict[str, str] = {
    # Verdict states
    "success": "#90EE90",
    "failed": "#FFB6C1",
    "missing_result": "#FFFFAD",
    "undecided": "#B0C4DE",
    # Backward-compat aliases for old reports
    "passed": "#90EE90",
    "dependencies_failed": "#D3D3D3",
    "passed+dependencies_failed": "#FFFFAD",
    "failed+dependencies_failed": "#FFB6C1",
    "mixed": "#FFFFAD",
    "no_tests": "#D3D3D3",
    "not_run": "#B0C4DE",
}

# Lifecycle state color mapping
LIFECYCLE_COLORS: dict[str, str] = {
    "new": "#87CEEB",
    "burning_in": "#FFD700",
    "stable": "#90EE90",
    "flaky": "#FFB6C1",
    "disabled": "#D3D3D3",
}

# Lifecycle state display labels
LIFECYCLE_LABELS: dict[str, str] = {
    "new": "NEW",
    "burning_in": "BURNING IN",
    "stable": "STABLE",
    "flaky": "FLAKY",
    "disabled": "DISABLED",
}

# Status display labels (verdict states + backward-compat aliases)
STATUS_LABELS: dict[str, str] = {
    # Verdict states
    "success": "SUCCESS",
    "failed": "FAILED",
    "missing_result": "MISSING RESULT",
    "undecided": "UNDECIDED",
    # Backward-compat aliases for old reports
    "passed": "PASSED",
    "dependencies_failed": "DEPS FAILED",
    "passed+dependencies_failed": "PASSED (deps failed)",
    "failed+dependencies_failed": "FAILED (deps failed)",
    "mixed": "MIXED",
    "no_tests": "NO TESTS",
    "not_run": "NOT RUN",
}

_CSS = """\
html, body {
    height: 100%;
    margin: 0;
}
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    padding: 20px;
    background: #f5f5f5;
    color: #333;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
}
.report-header {
    background: #fff;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.report-header h1 {
    margin: 0 0 10px 0;
    font-size: 24px;
}
.meta {
    color: #666;
    font-size: 14px;
}
.summary {
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    margin-top: 15px;
}
.summary-item {
    padding: 10px 16px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 14px;
}
.trigger-context {
    margin-top: 12px;
    padding: 10px 14px;
    background: #f4f6f9;
    border-radius: 6px;
    font-size: 13px;
    color: #444;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
}
.trigger-context .trigger-label {
    font-weight: 600;
    color: #222;
}
.trigger-context .trigger-pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    background: #e0e4ea;
    font-size: 12px;
    font-weight: 500;
}
.trigger-context .trigger-sep {
    color: #bbb;
}
.test-set-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}
.test-set-header h2, .test-set-header h3 {
    margin: 0;
}
.test-set-header h2 {
    font-size: 18px;
}
.test-set-header h3 {
    font-size: 16px;
}
.status-badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    color: #333;
}
.test-entry {
    border-left: 4px solid #ddd;
    margin: 8px 0;
    padding: 8px 12px;
    background: #fff;
    border-radius: 0 6px 6px 0;
}
.test-name {
    font-weight: 600;
    font-size: 14px;
}
.test-meta {
    font-size: 12px;
    color: #666;
    margin-top: 4px;
}
details.log-details {
    margin-top: 8px;
}
details.log-details > summary {
    cursor: pointer;
    font-size: 13px;
    color: #555;
    font-weight: 500;
}
details.log-details > summary:hover {
    color: #000;
}
.log-section {
    margin-top: 8px;
}
.log-section-label {
    font-size: 13px;
    color: #555;
    font-weight: 500;
    margin-bottom: 4px;
}
pre {
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 12px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 12px;
    line-height: 1.5;
    margin: 8px 0;
}
.measurements-table {
    border-collapse: collapse;
    width: 100%;
    font-size: 13px;
    margin: 8px 0;
}
.measurements-table th,
.measurements-table td {
    border: 1px solid #ddd;
    padding: 6px 10px;
    text-align: left;
}
.measurements-table th {
    background: #f0f0f0;
    font-weight: 600;
}
.burn-in-info {
    background: #e8f4fd;
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    margin: 8px 0;
}
.history-timeline {
    display: flex;
    gap: 2px;
    margin: 6px 0;
    overflow: hidden;
    max-width: 100%;
    align-items: center;
}
.history-timeline .ht-commit {
    display: flex;
    gap: 1px;
    align-items: center;
    padding: 3px 0;
    border-radius: 2px;
}
.history-timeline .ht-commit-a {
    background: #e0e0e0;
}
.history-timeline .ht-commit-b {
    background: #ccc;
}
.history-timeline .ht-box {
    flex: 0 0 4px;
    height: 16px;
    border-radius: 1px;
    cursor: default;
}
.history-timeline .ht-box:hover {
    outline: 2px solid #333;
    outline-offset: -1px;
    z-index: 1;
}
.regression-section {
    background: #fff;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.regression-section h2 {
    margin: 0 0 12px 0;
    font-size: 18px;
}
.flat-tests {
    background: #fff;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.flat-tests h2 {
    margin: 0 0 12px 0;
    font-size: 18px;
}
.e-value-verdict {
    background: #fff;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}
.e-value-verdict h2 {
    margin: 0 0 12px 0;
    font-size: 18px;
}
.verdict-badge {
    display: inline-block;
    padding: 6px 16px;
    border-radius: 12px;
    font-size: 16px;
    font-weight: 700;
    color: #333;
    margin-bottom: 12px;
}
.e-value-stats {
    font-size: 13px;
    color: #555;
    margin: 8px 0;
    line-height: 1.6;
}
.e-value-stats code {
    background: #f0f0f0;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 12px;
}
.lifecycle-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    color: #333;
    margin-left: 6px;
}
.lifecycle-reliability {
    font-size: 12px;
    color: #555;
    margin-left: 8px;
}
.lifecycle-summary {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin: 6px 0;
    align-items: center;
    font-size: 12px;
}
.lifecycle-summary-item {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    padding: 2px 8px;
    border-radius: 10px;
    font-weight: 600;
    font-size: 11px;
    color: #333;
}
.lifecycle-config-note {
    font-size: 11px;
    color: #888;
    margin-top: 2px;
}
.block-segment {
    border-radius: 6px;
    padding: 10px 12px;
    margin: 6px 0;
    border-left: 3px solid #ccc;
    background: #fafafa;
}
.block-segment.block-rigging {
    border-left-color: #6c757d;
}
.block-segment.block-stimulation {
    border-left-color: #0d6efd;
}
.block-segment.block-checkpoint {
    border-left-color: #ffc107;
}
.block-segment.block-verdict {
    border-left-color: #198754;
}
.block-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    font-size: 13px;
    font-weight: 600;
}
.block-type-badge {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 8px;
    font-size: 11px;
    font-weight: 600;
    color: #fff;
    text-transform: uppercase;
}
.block-type-badge.bt-rigging { background: #6c757d; }
.block-type-badge.bt-stimulation { background: #0d6efd; }
.block-type-badge.bt-checkpoint { background: #ffc107; color: #333; }
.block-type-badge.bt-verdict { background: #198754; }
.block-description {
    font-weight: 400;
    color: #555;
}
.block-features {
    font-size: 12px;
    color: #555;
    margin: 4px 0;
}
.assertion-list {
    list-style: none;
    padding: 0;
    margin: 4px 0;
    font-size: 13px;
}
.assertion-list li {
    padding: 2px 0;
}
.assertion-pass::before {
    content: '\\2713 ';
    color: #198754;
    font-weight: 700;
}
.assertion-fail::before {
    content: '\\2717 ';
    color: #cf222e;
    font-weight: 700;
}
.block-error {
    color: #cf222e;
    font-size: 13px;
    font-weight: 600;
    margin: 4px 0;
}
.step-segment {
    border-radius: 4px;
    padding: 6px 10px;
    margin: 4px 0 4px 12px;
    border-left: 2px solid #ccc;
    background: #f8f8f8;
}
.step-segment.step-passed {
    border-left-color: #90EE90;
}
.step-segment.step-failed {
    border-left-color: #FFB6C1;
}
.step-segment.step-warning {
    border-left-color: #FFFFAD;
}
.step-header {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    cursor: pointer;
}
.step-status-badge {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 6px;
    font-size: 10px;
    font-weight: 600;
    color: #333;
}
.step-name {
    font-size: 11px;
    color: #888;
    font-family: monospace;
}
.step-substep-count {
    font-size: 10px;
    color: #888;
    font-weight: normal;
    margin-left: 4px;
}
.block-features-list {
    list-style: none;
    padding: 0 0 0 8px;
    margin: 2px 0;
    font-size: 12px;
    color: #555;
}
.block-features-list li {
    padding: 1px 0;
}
.source-link {
    font-size: 11px;
    color: #888;
    text-decoration: none;
    margin-left: 8px;
    font-family: monospace;
}
a.source-link:hover {
    color: #0d6efd;
    text-decoration: underline;
}
"""


def generate_html_report(report_data: dict[str, Any]) -> str:
    """Generate a self-contained HTML report from report data.

    Args:
        report_data: Report dict (as produced by Reporter.generate_report()).
                     Expected structure: {"report": {...}}.

    Returns:
        Complete HTML string.
    """
    report = report_data.get("report", {})
    source_link_base = report.get("source_link_base")
    parts: list[str] = []

    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="UTF-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    parts.append("<title>Test Report</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append("</head>")
    parts.append("<body>")

    # Header
    parts.append(_render_header(report))

    history = report.get("history", {})
    lifecycle_config = report.get("lifecycle_config")

    # DAG visualization (only for hierarchical reports)
    if "test_set" in report:
        parts.append(_render_dag_section(
            report,
            history=history,
            lifecycle_config=lifecycle_config,
            source_link_base=source_link_base,
        ))

    # Flat tests (non-hierarchical reports only; hierarchical data is
    # rendered as hidden elements inside the DAG section above)
    if "test_set" not in report and "tests" in report:
        parts.append(_render_flat_tests(
            report["tests"], history,
            source_link_base=source_link_base,
        ))

    # Hash filter summary section
    if "hash_filter" in report:
        parts.append(_render_hash_filter_section(report["hash_filter"]))

    # Regression selection section
    if "regression_selection" in report:
        parts.append(_render_regression_selection(report["regression_selection"]))

    parts.append("</body>")
    parts.append("</html>")

    return "\n".join(parts)


def generate_html_from_file(report_path: Path) -> str:
    """Generate HTML report from a JSON report file.

    Args:
        report_path: Path to the JSON report file.

    Returns:
        Complete HTML string.

    Raises:
        FileNotFoundError: If report file doesn't exist.
        json.JSONDecodeError: If JSON is invalid.
    """
    with open(report_path) as f:
        report_data = json.load(f)
    return generate_html_report(report_data)


def write_html_report(
    report_data: dict[str, Any], output_path: Path
) -> None:
    """Write HTML report to a file.

    Args:
        report_data: Report dict.
        output_path: Path to write the HTML file.
    """
    html_content = generate_html_report(report_data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html_content)


def _render_header(report: dict[str, Any]) -> str:
    """Render the report header with summary."""
    parts: list[str] = []
    parts.append('<div class="report-header">')
    parts.append("<h1>Test Report</h1>")

    meta_parts: list[str] = []
    if "generated_at" in report:
        meta_parts.append(f"Generated: {html.escape(str(report['generated_at']))}")
    if "commit" in report:
        meta_parts.append(f"Commit: {html.escape(str(report['commit']))}")
    if meta_parts:
        parts.append(f'<div class="meta">{" | ".join(meta_parts)}</div>')

    parts.append(_render_trigger_context(report))

    summary = report.get("summary", {})
    if summary:
        parts.append('<div class="summary">')
        total = summary.get("total", 0)
        success = summary.get("success", summary.get("passed", 0))
        failed = summary.get("failed", 0)
        duration = summary.get("total_duration_seconds", 0)

        parts.append(
            f'<div class="summary-item" style="background:#e8e8e8">'
            f"Total: {total}</div>"
        )
        if success:
            parts.append(
                f'<div class="summary-item" style="background:#90EE90">'
                f"Success: {success}</div>"
            )
        if failed:
            parts.append(
                f'<div class="summary-item" style="background:#FFB6C1">'
                f"Failed: {failed}</div>"
            )
        missing = summary.get("missing_result", 0)
        if missing:
            parts.append(
                f'<div class="summary-item" style="background:#FFFFAD">'
                f"Missing Result: {missing}</div>"
            )
        undecided = summary.get("undecided", summary.get("not_run", 0))
        if undecided:
            parts.append(
                f'<div class="summary-item" style="background:#B0C4DE">'
                f"Undecided: {undecided}</div>"
            )
        parts.append(
            f'<div class="summary-item" style="background:#e8e8e8">'
            f"Duration: {duration:.3f}s</div>"
        )
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


def _render_trigger_context(report: dict[str, Any]) -> str:
    """Render a compact context banner showing how the report was generated."""
    parts: list[str] = ['<div class="trigger-context">']

    # Trigger source
    ci_gate = report.get("ci_gate_name")
    test_set_name = report.get("test_set", {}).get("name")
    if ci_gate:
        parts.append(
            f'<span class="trigger-label">CI Gate:</span> '
            f"{html.escape(str(ci_gate))}"
        )
    elif test_set_name:
        parts.append(
            '<span class="trigger-label">Test Set:</span> '
            f"{html.escape(str(test_set_name))}"
        )
    else:
        parts.append(
            '<span class="trigger-label">Direct invocation</span>'
        )

    # If we showed ci_gate and there's also a test set, show it too
    if ci_gate and test_set_name:
        parts.append('<span class="trigger-sep">&middot;</span>')
        parts.append(
            f'<span class="trigger-label">Test Set:</span> '
            f"{html.escape(str(test_set_name))}"
        )

    # Execution mode pill
    mode = report.get("execution_mode")
    if mode:
        parts.append('<span class="trigger-sep">&middot;</span>')
        parts.append(
            f'<span class="trigger-pill">{html.escape(mode)}</span>'
        )

    # Effort pill
    effort = report.get("effort", {})
    effort_mode = effort.get("mode") if isinstance(effort, dict) else None
    if effort_mode:
        parts.append(
            f'<span class="trigger-pill">effort: {html.escape(effort_mode)}</span>'
        )

    # Hash filter pill
    hf = report.get("hash_filter")
    if isinstance(hf, dict):
        skipped = hf.get("skipped", 0)
        total = hf.get("changed", 0) + hf.get("unchanged", 0)
        if total > 0:
            parts.append(
                f'<span class="trigger-pill">'
                f"hash-skip: {skipped}/{total}</span>"
            )

    parts.append("</div>")
    return "\n".join(parts)


def _render_test_entry(
    name: str, data: dict[str, Any],
    history_entries: list[dict[str, Any]] | None = None,
    source_link_base: str | None = None,
    *,
    effort_classification: dict[str, Any] | None = None,
    e_value_per_test: dict[str, Any] | None = None,
) -> str:
    """Render a single test entry with expandable details."""
    parts: list[str] = []
    status = data.get("status", "success")
    color = STATUS_COLORS.get(status, "#e8e8e8")
    label = STATUS_LABELS.get(status, status.upper())
    duration = data.get("duration_seconds", 0)
    assertion = data.get("assertion", "")

    parts.append(
        f'<div class="test-entry" style="border-left-color:{color}"'
        f' data-test-name="{html.escape(name, quote=True)}">'
    )

    lifecycle_html = ""
    lifecycle = data.get("lifecycle")
    if lifecycle:
        lifecycle_html = " " + _render_lifecycle_badge(lifecycle)

    parts.append(
        f'<div class="test-name">{html.escape(name)} '
        f'<span class="status-badge" style="background:{color}">'
        f"{html.escape(label)}</span>"
        f"{lifecycle_html}</div>"
    )

    # Parameters table
    parameters = data.get("parameters")
    if parameters:
        parts.append('<table class="measurements-table" style="margin-top:6px">')
        parts.append("<tr><th>Parameter</th><th>Value</th></tr>")
        for key, value in parameters.items():
            parts.append(
                f"<tr><td>{html.escape(str(key))}</td>"
                f"<td>{html.escape(str(value))}</td></tr>"
            )
        parts.append("</table>")

    # History timeline
    if history_entries:
        parts.append(_render_history_timeline(history_entries))

    meta_items: list[str] = []
    if assertion:
        meta_items.append(f"Assertion: {html.escape(assertion)}")
    meta_items.append(f"Duration: {duration:.3f}s")
    if data.get("exit_code") is not None:
        meta_items.append(f"Exit code: {data['exit_code']}")
    if meta_items:
        parts.append(f'<div class="test-meta">{" | ".join(meta_items)}</div>')

    # Stdout/stderr logs
    stdout = data.get("stdout", "")
    stderr = data.get("stderr", "")
    if stdout or stderr:
        parts.append('<div class="log-section">')
        if stdout:
            segments = parse_stdout_segments(stdout)
            has_blocks = any(isinstance(s, BlockSegment) for s in segments)
            if has_blocks:
                parts.append(_render_stdout_segments(
                    segments, source_link_base=source_link_base,
                ))
            else:
                parts.append(f"<pre>{html.escape(stdout)}</pre>")
        if stderr:
            parts.append(f'<pre style="border-left:3px solid #FFB6C1">'
                         f"{html.escape(stderr)}</pre>")
        parts.append("</div>")

    # Burn-in progress
    burn_in = data.get("burn_in")
    if burn_in:
        parts.append(_render_burn_in(burn_in))

    # Inferred dependencies
    inferred_deps = data.get("inferred_dependencies")
    if inferred_deps:
        parts.append(_render_inferred_deps(inferred_deps))

    # Effort classification badge
    if effort_classification:
        cls = effort_classification.get("classification", "")
        cls_color = _CLASSIFICATION_COLORS.get(cls, "#FFFFFF")
        initial = effort_classification.get("initial_status", "")
        runs = effort_classification.get("runs", 0)
        passes = effort_classification.get("passes", 0)
        sprt = effort_classification.get("sprt_decision", "")
        parts.append('<div style="margin-top:6px">')
        parts.append(
            f'<strong>Effort:</strong> '
            f'<span class="status-badge" style="background:{cls_color}">'
            f'{html.escape(cls)}</span>'
        )
        parts.append(
            f' &mdash; initial: {html.escape(initial)}, '
            f'{passes}/{runs} passed'
        )
        if sprt and sprt != "not_evaluated":
            parts.append(f', SPRT: {html.escape(sprt)}')
        parts.append("</div>")

    # E-value evidence
    if e_value_per_test:
        s_val = e_value_per_test.get("s_value", 0)
        runs = e_value_per_test.get("runs", 0)
        passes = e_value_per_test.get("passes", 0)
        commits = e_value_per_test.get("commits_included", 0)
        parts.append(
            f'<div style="margin-top:4px">'
            f'<strong>E-value:</strong> S_i = <code>{s_val:.4f}</code>, '
            f'{runs} runs, {passes} passes, {commits} commits</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)


def _render_stdout_segments(
    segments: list[TextSegment | BlockSegment],
    source_link_base: str | None = None,
) -> str:
    """Render parsed stdout segments as unified HTML."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSegment):
            text = seg.text.strip()
            if text:
                parts.append(f"<pre>{html.escape(text)}</pre>")
        elif isinstance(seg, BlockSegment):
            parts.append(_render_block_segment(
                seg, source_link_base=source_link_base,
            ))
    return "\n".join(parts)


def _step_should_expand(step: StepSegment) -> bool:
    """Return True if this step or any descendant has non-passed status."""
    if step.status != "passed":
        return True
    return any(_step_should_expand(sub) for sub in step.steps)


# Step status badge colors
_STEP_STATUS_COLORS: dict[str, str] = {
    "passed": "#90EE90",
    "failed": "#FFB6C1",
    "warning": "#FFFFAD",
}

# Step status display labels
_STEP_STATUS_LABELS: dict[str, str] = {
    "passed": "PASSED",
    "failed": "FAILED",
    "warning": "WARNING",
}


def _render_step_segment(
    step: StepSegment,
    source_link_base: str | None = None,
) -> str:
    """Render a single step as a collapsible HTML element.

    Passed steps are collapsed by default; failed and warning steps
    (and their ancestors) are expanded.
    """
    status = step.status
    should_expand = _step_should_expand(step)
    open_attr = " open" if should_expand else ""
    status_color = _STEP_STATUS_COLORS.get(status, "#e8e8e8")
    status_label = _STEP_STATUS_LABELS.get(status, status.upper())

    parts: list[str] = []
    parts.append(
        f'<details class="step-segment step-{html.escape(status)}"{open_attr}>'
    )
    substep_indicator = ""
    if step.steps:
        n = len(step.steps)
        substep_indicator = (
            f' <span class="step-substep-count">'
            f'({n} sub-step{"s" if n != 1 else ""})</span>'
        )
    parts.append(
        f'<summary class="step-header">'
        f'<span class="step-status-badge" '
        f'style="background:{status_color}">'
        f'{html.escape(status_label)}</span> '
        f'{html.escape(step.description)} '
        f'<span class="step-name">{html.escape(step.step)}</span>'
        f'{substep_indicator}'
        f'</summary>'
    )

    # Features
    if step.features:
        feat_parts: list[str] = []
        for f in step.features:
            name_html = html.escape(f.get("name", ""))
            link = render_source_link(f, source_link_base)
            feat_parts.append(f"{name_html}{link}")
        parts.append(
            f'<div class="block-features">Features: '
            f'{", ".join(feat_parts)}</div>'
        )

    # Measurements table
    if step.measurements:
        parts.append('<table class="measurements-table">')
        parts.append(
            "<tr><th>Name</th><th>Value</th><th>Unit</th><th>Source</th></tr>"
        )
        for m in step.measurements:
            mname = html.escape(str(m.get("name", "")))
            mval = html.escape(str(m.get("value", "")))
            munit = html.escape(str(m.get("unit", "")))
            mlink = render_source_link(m, source_link_base)
            parts.append(
                f"<tr><td>{mname}</td><td>{mval}</td>"
                f"<td>{munit}</td><td>{mlink}</td></tr>"
            )
        parts.append("</table>")

    # Assertions
    if step.assertions:
        parts.append('<ul class="assertion-list">')
        for a in step.assertions:
            desc = html.escape(str(a.get("description", "")))
            a_status = a.get("status", "unknown")
            css_class = (
                "assertion-pass" if a_status == "passed"
                else "assertion-fail"
            )
            link = render_source_link(a, source_link_base)
            parts.append(f'<li class="{css_class}">{desc}{link}</li>')
        parts.append("</ul>")

    # Errors
    for err in step.errors:
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        if msg:
            link = (
                render_source_link(err, source_link_base)
                if isinstance(err, dict) else ""
            )
            parts.append(
                f'<div class="block-error">Error: '
                f'{html.escape(msg)}{link}</div>'
            )

    # Nested sub-steps
    for sub in step.steps:
        parts.append(_render_step_segment(sub, source_link_base))

    # Raw logs (collapsed)
    if step.logs:
        parts.append('<details class="log-details">')
        parts.append("<summary>Raw logs</summary>")
        parts.append(f"<pre>{html.escape(step.logs)}</pre>")
        parts.append("</details>")

    parts.append("</details>")
    return "\n".join(parts)


def _render_block_segment(
    block: BlockSegment,
    source_link_base: str | None = None,
) -> str:
    """Render a single structured block as an HTML card."""
    btype = block.block
    parts: list[str] = []
    parts.append(f'<div class="block-segment block-{html.escape(btype)}">')

    # Header: block type badge + optional description
    parts.append('<div class="block-header">')
    parts.append(
        f'<span class="block-type-badge bt-{html.escape(btype)}">'
        f"{html.escape(btype)}</span>"
    )
    if block.description:
        parts.append(
            f'<span class="block-description">'
            f"{html.escape(block.description)}</span>"
        )
    parts.append("</div>")

    # Compute step prefixes for partitioning direct vs step-qualified items
    step_prefixes = {s.step + "." for s in block.steps}

    # Features
    if block.features:
        if btype == "rigging":
            parts.append('<div class="block-features">Features:</div>')
            parts.append('<ul class="block-features-list">')
            for f in block.features:
                name_html = html.escape(f.get("name", ""))
                link = render_source_link(f, source_link_base)
                parts.append(f"<li>{name_html}{link}</li>")
            parts.append("</ul>")
        else:
            feat_parts: list[str] = []
            for f in block.features:
                name_html = html.escape(f.get("name", ""))
                link = render_source_link(f, source_link_base)
                feat_parts.append(f"{name_html}{link}")
            parts.append(
                f'<div class="block-features">Features: '
                f'{", ".join(feat_parts)}</div>'
            )

    # Measurements table (split direct vs step-qualified)
    if block.measurements:
        direct_m = [
            m for m in block.measurements
            if not any(
                m.get("name", "").startswith(p) for p in step_prefixes
            )
        ]
        step_m = [
            m for m in block.measurements
            if any(
                m.get("name", "").startswith(p) for p in step_prefixes
            )
        ]
        if direct_m:
            parts.append('<table class="measurements-table">')
            parts.append(
                "<tr><th>Name</th><th>Value</th><th>Unit</th>"
                "<th>Source</th></tr>"
            )
            for m in direct_m:
                mname = html.escape(str(m.get("name", "")))
                mval = html.escape(str(m.get("value", "")))
                munit = html.escape(str(m.get("unit", "")))
                mlink = render_source_link(m, source_link_base)
                parts.append(
                    f"<tr><td>{mname}</td><td>{mval}</td>"
                    f"<td>{munit}</td><td>{mlink}</td></tr>"
                )
            parts.append("</table>")
        if step_m:
            parts.append('<details class="log-details">')
            parts.append(
                f"<summary>Sub-step measurements ({len(step_m)})"
                f"</summary>"
            )
            parts.append('<table class="measurements-table">')
            parts.append(
                "<tr><th>Name</th><th>Value</th><th>Unit</th>"
                "<th>Source</th></tr>"
            )
            for m in step_m:
                mname = html.escape(str(m.get("name", "")))
                mval = html.escape(str(m.get("value", "")))
                munit = html.escape(str(m.get("unit", "")))
                mlink = render_source_link(m, source_link_base)
                parts.append(
                    f"<tr><td>{mname}</td><td>{mval}</td>"
                    f"<td>{munit}</td><td>{mlink}</td></tr>"
                )
            parts.append("</table>")
            parts.append("</details>")

    # Assertions (split direct vs step-qualified)
    if block.assertions:
        direct_a = [
            a for a in block.assertions
            if not any(
                a.get("description", "").startswith(p)
                for p in step_prefixes
            )
        ]
        step_a = [
            a for a in block.assertions
            if any(
                a.get("description", "").startswith(p)
                for p in step_prefixes
            )
        ]
        if direct_a:
            parts.append('<ul class="assertion-list">')
            for a in direct_a:
                desc = html.escape(str(a.get("description", "")))
                status = a.get("status", "unknown")
                css_class = (
                    "assertion-pass" if status == "passed"
                    else "assertion-fail"
                )
                link = render_source_link(a, source_link_base)
                parts.append(f'<li class="{css_class}">{desc}{link}</li>')
            parts.append("</ul>")
        if step_a:
            parts.append('<details class="log-details">')
            parts.append(
                f"<summary>Sub-step checks ({len(step_a)})</summary>"
            )
            parts.append('<ul class="assertion-list">')
            for a in step_a:
                desc = html.escape(str(a.get("description", "")))
                status = a.get("status", "unknown")
                css_class = (
                    "assertion-pass" if status == "passed"
                    else "assertion-fail"
                )
                link = render_source_link(a, source_link_base)
                parts.append(f'<li class="{css_class}">{desc}{link}</li>')
            parts.append("</ul>")
            parts.append("</details>")

    # Steps (rendered as nested collapsible sections)
    for step in block.steps:
        parts.append(_render_step_segment(step, source_link_base))

    # Block logs (raw timeline -- collapsed by default)
    if block.logs:
        parts.append('<details class="log-details">')
        parts.append("<summary>Raw logs</summary>")
        parts.append(f"<pre>{html.escape(block.logs)}</pre>")
        parts.append("</details>")

    # Errors
    for err in block.errors:
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
        if msg:
            link = render_source_link(err, source_link_base) if isinstance(err, dict) else ""
            parts.append(
                f'<div class="block-error">Error: {html.escape(msg)}{link}</div>'
            )

    parts.append("</div>")
    return "\n".join(parts)


# History timeline status-to-color mapping (verdict + backward-compat)
_TIMELINE_COLORS: dict[str, str] = {
    # Verdict states
    "success": "#2da44e",
    "failed": "#cf222e",
    "missing_result": "#d4a72c",
    "undecided": "#B0C4DE",
    # Backward-compat aliases for old history entries
    "passed": "#2da44e",
    "dependencies_failed": "#999",
    "passed+dependencies_failed": "#d4a72c",
    "failed+dependencies_failed": "#cf222e",
    "mixed": "#d4a72c",
    "no_tests": "#999",
}


def _render_history_timeline(entries: list[dict[str, Any]]) -> str:
    """Render a compact horizontal pass/fail history timeline.

    Each entry becomes a small colored box.  Consecutive entries that
    share the same commit are grouped inside a commit wrapper that
    alternates between two background colors and extends slightly above
    and below the status boxes, making commit boundaries visible.

    Entries are displayed in chronological order (oldest left, newest
    right).
    """
    if not entries:
        return ""

    # Group consecutive entries by commit identity.
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    prev_commit: str | None = None
    for entry in entries:
        commit = entry.get("commit", "")
        if commit != prev_commit:
            groups.append((commit, []))
        groups[-1][1].append(entry)
        prev_commit = commit

    parts: list[str] = []
    parts.append('<div class="history-timeline">')
    for idx, (commit_key, group) in enumerate(groups):
        cls = "ht-commit-a" if idx % 2 == 0 else "ht-commit-b"
        commit_tip = html.escape(commit_key[:12]) if commit_key else ""
        title_attr = f' title="{commit_tip}"' if commit_tip else ""
        parts.append(f'<div class="ht-commit {cls}"{title_attr}>')
        for entry in group:
            status = entry.get("status", "success")
            color = _TIMELINE_COLORS.get(status, "#999")
            entry_commit = entry.get("commit", "")
            tooltip = (
                html.escape(entry_commit[:12])
                if entry_commit
                else html.escape(status)
            )
            parts.append(
                f'<div class="ht-box" style="background:{color}" '
                f'title="{tooltip}"></div>'
            )
        parts.append("</div>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_burn_in(burn_in: dict[str, Any]) -> str:
    """Render burn-in progress information."""
    runs = burn_in.get("runs", 0)
    passes = burn_in.get("passes", 0)
    sprt_status = burn_in.get("sprt_status", "unknown")
    return (
        f'<div class="burn-in-info">'
        f"Burn-in: {runs} runs, {passes} passes, "
        f"SPRT: {html.escape(sprt_status)}</div>"
    )


def _render_inferred_deps(deps: list[dict[str, Any]]) -> str:
    """Render inferred dependencies section."""
    parts: list[str] = []
    parts.append('<div class="log-section">')
    parts.append('<div class="log-section-label">Inferred Dependencies</div>')
    parts.append("<ul>")
    for dep in deps:
        dep_str = html.escape(str(dep.get("name", dep)))
        parts.append(f"<li>{dep_str}</li>")
    parts.append("</ul>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_lifecycle_badge(lifecycle: dict[str, Any]) -> str:
    """Render a lifecycle state badge with reliability rate."""
    state = lifecycle.get("state", "new")
    color = LIFECYCLE_COLORS.get(state, "#e8e8e8")
    label = LIFECYCLE_LABELS.get(state, state.upper())
    runs = lifecycle.get("runs", 0)
    passes = lifecycle.get("passes", 0)
    reliability = lifecycle.get("reliability", 0.0)

    parts: list[str] = []
    parts.append(
        f'<span class="lifecycle-badge" style="background:{color}">'
        f"{html.escape(label)}</span>"
    )
    if runs > 0:
        pct = f"{reliability * 100:.1f}%"
        parts.append(
            f'<span class="lifecycle-reliability">'
            f"{html.escape(pct)} ({passes}/{runs})</span>"
        )
    return "".join(parts)


def _render_lifecycle_summary(
    summary: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str:
    """Render a lifecycle summary for a test set node."""
    parts: list[str] = []
    parts.append('<div class="lifecycle-summary">')

    for state_name in ("stable", "burning_in", "flaky", "new", "disabled"):
        count = summary.get(state_name, 0)
        if count > 0:
            color = LIFECYCLE_COLORS.get(state_name, "#e8e8e8")
            label = LIFECYCLE_LABELS.get(state_name, state_name.upper())
            parts.append(
                f'<span class="lifecycle-summary-item" '
                f'style="background:{color}">'
                f"{count} {html.escape(label)}</span>"
            )

    agg_runs = summary.get("aggregate_runs", 0)
    agg_passes = summary.get("aggregate_passes", 0)
    agg_reliability = summary.get("aggregate_reliability", 0.0)
    if agg_runs > 0:
        pct = f"{agg_reliability * 100:.1f}%"
        parts.append(
            f'<span class="lifecycle-reliability">'
            f"Reliability: {html.escape(pct)} ({agg_passes}/{agg_runs})"
            f"</span>"
        )

    parts.append("</div>")

    if config:
        min_rel = config.get("min_reliability", 0)
        sig = config.get("statistical_significance", 0)
        parts.append(
            f'<div class="lifecycle-config-note">'
            f"Threshold: {min_rel*100:.0f}% reliability "
            f"at {sig*100:.0f}% confidence</div>"
        )

    return "\n".join(parts)


def _collect_test_names(test_set: dict[str, Any]) -> list[str]:
    """Recursively collect all test names from a test set."""
    names = list(test_set.get("tests", {}).keys())
    for subset in test_set.get("subsets", []):
        names.extend(_collect_test_names(subset))
    return names


def _compute_set_history(
    test_names: list[str],
    history: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Aggregate per-test histories into a set-level timeline.

    Uses positional indexing: entry *i* in each test's history list
    corresponds to the *i*-th run.  At each position the aggregate
    status is: failed if any test failed, dependencies_failed if all
    grey, passed otherwise.  Tests added later simply have shorter
    histories and are skipped for earlier positions.
    """
    if not test_names or not history:
        return []

    max_len = max(
        (len(history.get(name, [])) for name in test_names),
        default=0,
    )
    if max_len == 0:
        return []

    _FAILED_STATUSES = frozenset({
        "failed", "failed+dependencies_failed",
    })
    _GREY_STATUSES = frozenset({
        "dependencies_failed", "no_tests",
        "missing_result", "undecided",
    })

    result: list[dict[str, Any]] = []
    for i in range(max_len):
        statuses: list[str] = []
        commit: str | None = None
        for name in test_names:
            entries = history.get(name, [])
            if i < len(entries):
                statuses.append(entries[i].get("status", "success"))
                if commit is None:
                    commit = entries[i].get("commit")

        if not statuses:
            continue

        if any(s in _FAILED_STATUSES for s in statuses):
            agg = "failed"
        elif all(s in _GREY_STATUSES for s in statuses):
            agg = "dependencies_failed"
        else:
            agg = "success"

        entry: dict[str, Any] = {"status": agg}
        if commit:
            entry["commit"] = commit
        result.append(entry)

    return result


def _render_set_summary_card(
    test_set: dict[str, Any],
    lifecycle_config: dict[str, Any] | None = None,
    history_entries: list[dict[str, Any]] | None = None,
    *,
    e_value_verdict: dict[str, Any] | None = None,
    effort_data: dict[str, Any] | None = None,
    ci_gate_name: str | None = None,
) -> str:
    """Render a hidden summary card for the DAG detail pane.

    The card carries a ``data-set-name`` attribute so the JavaScript
    click handler on group nodes can locate and clone it into the
    detail pane, mirroring the pattern used for test entries.
    """
    name = test_set.get("name", "Test Set")
    status = test_set.get("status", "success")
    assertion = test_set.get("assertion", "")
    color = STATUS_COLORS.get(status, "#e8e8e8")
    label = STATUS_LABELS.get(status, status.upper())

    parts: list[str] = []
    parts.append(
        f'<div data-set-name="{html.escape(name, quote=True)}"'
        f' style="display:none">'
    )
    parts.append('<div class="test-set-header">')
    parts.append(f"<h2>{html.escape(name)}</h2>")
    parts.append(
        f'<span class="status-badge" style="background:{color}">'
        f"{html.escape(label)}</span>"
    )
    parts.append("</div>")

    if assertion:
        parts.append(
            f'<div class="test-meta">Assertion: {html.escape(assertion)}</div>'
        )

    lifecycle_summary = test_set.get("lifecycle_summary")
    if lifecycle_summary:
        parts.append(_render_lifecycle_summary(lifecycle_summary, lifecycle_config))

    if history_entries:
        parts.append(_render_history_timeline(history_entries))

    # Inline e-value verdict on the root test_set (the one directly under the executing gate)
    if e_value_verdict:
        parts.append(_render_inline_e_value_verdict(e_value_verdict))

    # Inline effort summary
    if effort_data:
        parts.append(_render_inline_effort_summary(effort_data))

    parts.append("</div>")
    return "\n".join(parts)


def _render_ci_gate_card(
    node: dict[str, Any],
    *,
    e_value_verdict: dict[str, Any] | None = None,
    effort_data: dict[str, Any] | None = None,
    ci_gate_name: str | None = None,
) -> str:
    """Render a hidden detail card for a ci_gate DAG node.

    The card carries a ``data-ci-gate-name`` attribute so the JavaScript
    tap handler on ci_gate nodes can locate and clone it.
    """
    name = node.get("name", "CI Gate")
    status = node.get("status", "undecided")
    color = STATUS_COLORS.get(status, "#e8e8e8")
    label = STATUS_LABELS.get(status, status.upper())
    params = node.get("ci_gate_params", {})
    is_executing = ci_gate_name is not None and name == ci_gate_name

    parts: list[str] = []
    parts.append(
        f'<div data-ci-gate-name="{html.escape(name, quote=True)}"'
        f' style="display:none">'
    )
    parts.append('<div class="test-set-header">')
    parts.append(f"<h2>{html.escape(name)}</h2>")
    parts.append(
        f'<span class="status-badge" style="background:{color}">'
        f"{html.escape(label)}</span>"
    )
    parts.append("</div>")

    # Execution parameters table
    if params:
        parts.append("<h3>Execution Parameters</h3>")
        parts.append('<table class="measurements-table">')
        parts.append("<tr><th>Parameter</th><th>Value</th></tr>")
        for pname, pinfo in sorted(params.items()):
            val = pinfo.get("value", "")
            is_default = pinfo.get("is_default", True)
            style = ' style="color:#999"' if is_default else ' style="font-weight:bold"'
            suffix = ""
            if is_default:
                suffix = ' <span style="color:#999;font-size:0.85em">(default)</span>'
            parts.append(
                f"<tr><td>{html.escape(str(pname))}</td>"
                f"<td{style}>{html.escape(str(val))}{suffix}</td></tr>"
            )
        parts.append("</table>")

    # E-value verdict (only for the executing gate)
    if is_executing and e_value_verdict:
        parts.append(_render_inline_e_value_verdict(e_value_verdict))

    # Effort summary (only for the executing gate)
    if is_executing and effort_data:
        parts.append(_render_inline_effort_summary(effort_data))

    parts.append("</div>")
    return "\n".join(parts)


def _render_inline_e_value_verdict(verdict_data: dict[str, Any]) -> str:
    """Render e-value verdict inline within a detail card."""
    parts: list[str] = []
    parts.append("<h3>E-value Verdict</h3>")

    verdict = verdict_data.get("verdict", "UNDECIDED")
    color = _VERDICT_COLORS.get(verdict, "#FFFFAD")
    parts.append(
        f'<span class="verdict-badge" style="background:{color};'
        f'padding:2px 8px;border-radius:4px">'
        f"{html.escape(verdict)}</span>"
    )

    parts.append('<div style="margin-top:6px">')
    e_set = verdict_data.get("e_set", 0)
    red_thresh = verdict_data.get("red_threshold", 0)
    min_s = verdict_data.get("min_s_value", 0)
    green_thresh = verdict_data.get("green_threshold", 0)
    n_tests = verdict_data.get("n_tests", 0)
    weakest = verdict_data.get("weakest_test", "")

    parts.append(
        f"<strong>Tests:</strong> {n_tests}<br>"
        f"<strong>RED:</strong> "
        f"<code>E_set = {e_set:.4f}</code> "
        f"(threshold: <code>{red_thresh:.4f}</code>)<br>"
        f"<strong>GREEN:</strong> "
        f"<code>min(S_i) = {min_s:.4f}</code> "
        f"(threshold: <code>{green_thresh:.4f}</code>)"
    )

    if weakest:
        parts.append(f"<br><strong>Weakest:</strong> {html.escape(str(weakest))}")

    total_reruns = verdict_data.get("total_reruns")
    if total_reruns is not None:
        parts.append(f"<br><strong>HiFi reruns:</strong> {total_reruns}")

    parts.append("</div>")

    # Per-test E-value table
    per_test = verdict_data.get("per_test", [])
    if per_test:
        parts.append("<details>")
        parts.append(f"<summary>Per-test E-values ({len(per_test)})</summary>")
        parts.append('<table class="measurements-table">')
        parts.append(
            "<tr><th>Test</th><th>E_i</th><th>S_i</th>"
            "<th>Runs</th><th>Passes</th><th>Commits</th></tr>"
        )
        for tv in per_test:
            tname = html.escape(str(tv.get("test_name", "")))
            e_val = tv.get("e_value", 0)
            s_val = tv.get("s_value", 0)
            runs = tv.get("runs", 0)
            passes = tv.get("passes", 0)
            commits = tv.get("commits_included", 0)
            parts.append(
                f"<tr><td>{tname}</td><td>{e_val:.4f}</td>"
                f"<td>{s_val:.4f}</td><td>{runs}</td>"
                f"<td>{passes}</td><td>{commits}</td></tr>"
            )
        parts.append("</table>")
        parts.append("</details>")

    return "\n".join(parts)


def _render_inline_effort_summary(effort_data: dict[str, Any]) -> str:
    """Render effort summary inline within a detail card."""
    parts: list[str] = []
    parts.append("<h3>Effort Classification</h3>")

    mode = effort_data.get("mode", "")
    total_reruns = effort_data.get("total_reruns", 0)
    max_reruns = effort_data.get("max_reruns_per_test", 0)
    parts.append(
        f"<strong>Mode:</strong> {html.escape(str(mode))}<br>"
        f"<strong>Total reruns:</strong> {total_reruns} "
        f"(budget: {max_reruns} per test)<br>"
    )

    classifications = effort_data.get("classifications", {})
    if classifications:
        counts: dict[str, int] = {}
        for c in classifications.values():
            cls = c.get("classification", "unknown")
            counts[cls] = counts.get(cls, 0) + 1
        summary_parts = [f"{count} {cls}" for cls, count in sorted(counts.items())]
        parts.append(f"<strong>Summary:</strong> {', '.join(summary_parts)}<br>")

    burn_in_sweep = effort_data.get("burn_in_sweep")
    if burn_in_sweep:
        sweep_total = burn_in_sweep.get("total_runs", 0)
        decided = burn_in_sweep.get("decided", {})
        undecided = burn_in_sweep.get("undecided", [])
        parts.append(
            f"<br><strong>Burn-in sweep:</strong> {sweep_total} runs, "
            f"{len(decided)} decided, {len(undecided)} still burning in"
        )

    return "\n".join(parts)


def _render_flat_tests(
    tests: list[dict[str, Any]],
    history: dict[str, list[dict[str, Any]]],
    source_link_base: str | None = None,
) -> str:
    """Render a flat (non-hierarchical) test list."""
    parts: list[str] = []
    parts.append('<div class="flat-tests">')
    parts.append("<h2>Test Results</h2>")
    for test in tests:
        name = test.get("name", "unknown")
        parts.append(_render_test_entry(
            name, test, history.get(name, []),
            source_link_base=source_link_base,
        ))
    parts.append("</div>")
    return "\n".join(parts)


_VERDICT_COLORS: dict[str, str] = {
    "GREEN": "#90EE90",
    "RED": "#FFB6C1",
    "UNDECIDED": "#FFFFAD",
}


def _render_e_value_verdict(verdict_data: dict[str, Any]) -> str:
    """Render E-value test set verdict section."""
    parts: list[str] = []
    parts.append('<div class="e-value-verdict">')
    parts.append("<h2>Test Set Verdict (E-values)</h2>")

    verdict = verdict_data.get("verdict", "UNDECIDED")
    color = _VERDICT_COLORS.get(verdict, "#FFFFAD")
    parts.append(
        f'<div class="verdict-badge" style="background:{color}">'
        f"{html.escape(verdict)}</div>"
    )

    parts.append('<div class="e-value-stats">')
    e_set = verdict_data.get("e_set", 0)
    red_thresh = verdict_data.get("red_threshold", 0)
    min_s = verdict_data.get("min_s_value", 0)
    green_thresh = verdict_data.get("green_threshold", 0)
    n_tests = verdict_data.get("n_tests", 0)
    weakest = verdict_data.get("weakest_test", "")

    parts.append(
        f"<strong>Tests evaluated:</strong> {n_tests}<br>"
        f"<strong>RED evidence:</strong> "
        f"<code>E_set = {e_set:.4f}</code> "
        f"(threshold: <code>{red_thresh:.4f}</code>)<br>"
        f"<strong>GREEN evidence:</strong> "
        f"<code>min(S_i) = {min_s:.4f}</code> "
        f"(threshold: <code>{green_thresh:.4f}</code>)"
    )

    if weakest:
        parts.append(f"<br><strong>Weakest test:</strong> {html.escape(str(weakest))}")

    total_reruns = verdict_data.get("total_reruns")
    if total_reruns is not None:
        parts.append(f"<br><strong>HiFi reruns:</strong> {total_reruns}")

    parts.append("</div>")

    # Per-test E-value table
    per_test = verdict_data.get("per_test", [])
    if per_test:
        parts.append("<details>")
        parts.append(f"<summary>Per-test E-values ({len(per_test)})</summary>")
        parts.append('<table class="measurements-table">')
        parts.append(
            "<tr><th>Test</th><th>E_i</th><th>S_i</th>"
            "<th>Runs</th><th>Passes</th><th>Commits</th></tr>"
        )
        for tv in per_test:
            tname = html.escape(str(tv.get("test_name", "")))
            e_val = tv.get("e_value", 0)
            s_val = tv.get("s_value", 0)
            runs = tv.get("runs", 0)
            passes = tv.get("passes", 0)
            commits = tv.get("commits_included", 0)
            parts.append(
                f"<tr><td>{tname}</td><td>{e_val:.4f}</td>"
                f"<td>{s_val:.4f}</td><td>{runs}</td>"
                f"<td>{passes}</td><td>{commits}</td></tr>"
            )
        parts.append("</table>")
        parts.append("</details>")

    parts.append("</div>")
    return "\n".join(parts)


_CLASSIFICATION_COLORS: dict[str, str] = {
    "true_pass": "#90EE90",
    "true_fail": "#FFB6C1",
    "flake": "#FFFFAD",
    "undecided": "#D3D3D3",
}


def _render_hash_filter_section(hash_data: dict[str, Any]) -> str:
    """Render hash-based filtering summary section."""
    parts: list[str] = []
    parts.append('<div class="e-value-verdict">')
    parts.append("<h2>Hash-Based Filtering</h2>")

    changed = hash_data.get("changed", 0)
    unchanged = hash_data.get("unchanged", 0)
    skipped = hash_data.get("skipped", 0)
    total = changed + unchanged

    parts.append(f"<strong>Total tests:</strong> {total}<br>")
    parts.append(
        f"<strong>Changed:</strong> {changed} "
        f"(target hash differs from last run)<br>"
    )
    parts.append(
        f"<strong>Unchanged:</strong> {unchanged} "
        f"(target hash matches last run)<br>"
    )
    if skipped > 0:
        parts.append(
            f"<strong>Skipped:</strong> {skipped} "
            f"(unchanged + conclusive SPRT, excluded from execution)<br>"
        )

    parts.append("</div>")
    return "\n".join(parts)


def _render_effort_section(effort_data: dict[str, Any]) -> str:
    """Render effort mode SPRT classification section."""
    parts: list[str] = []
    parts.append('<div class="e-value-verdict">')
    parts.append(f"<h2>Effort: {html.escape(str(effort_data.get('mode', '')))}</h2>")

    total_reruns = effort_data.get("total_reruns", 0)
    max_reruns = effort_data.get("max_reruns_per_test", 0)
    parts.append(
        f"<strong>Total reruns:</strong> {total_reruns} "
        f"(budget: {max_reruns} per test)<br>"
    )

    classifications = effort_data.get("classifications", {})
    if classifications:
        parts.append(f"<strong>Tests classified:</strong> {len(classifications)}<br>")

        # Summary counts
        counts: dict[str, int] = {}
        for c in classifications.values():
            cls = c.get("classification", "unknown")
            counts[cls] = counts.get(cls, 0) + 1
        summary_parts = [f"{count} {cls}" for cls, count in sorted(counts.items())]
        parts.append(f"<strong>Summary:</strong> {', '.join(summary_parts)}")

        # Classification table
        parts.append('<table class="measurements-table">')
        parts.append(
            "<tr><th>Test</th><th>Classification</th>"
            "<th>Initial</th><th>Runs</th><th>Passes</th><th>SPRT</th></tr>"
        )
        for test_name, c in sorted(classifications.items()):
            tname = html.escape(str(test_name))
            cls = c.get("classification", "")
            color = _CLASSIFICATION_COLORS.get(cls, "#FFFFFF")
            initial = c.get("initial_status", "")
            runs = c.get("runs", 0)
            passes = c.get("passes", 0)
            sprt = c.get("sprt_decision", "")
            parts.append(
                f"<tr><td>{tname}</td>"
                f'<td style="background:{color}">{html.escape(cls)}</td>'
                f"<td>{html.escape(initial)}</td>"
                f"<td>{runs}</td><td>{passes}</td>"
                f"<td>{html.escape(sprt)}</td></tr>"
            )
        parts.append("</table>")

    # Burn-in sweep results (if present)
    burn_in_sweep = effort_data.get("burn_in_sweep")
    if burn_in_sweep:
        sweep_total = burn_in_sweep.get("total_runs", 0)
        decided = burn_in_sweep.get("decided", {})
        undecided = burn_in_sweep.get("undecided", [])

        parts.append("<br><h3>Burn-in Sweep</h3>")
        parts.append(
            f"<strong>Sweep runs:</strong> {sweep_total}<br>"
        )
        if decided:
            parts.append(
                f"<strong>Decided:</strong> {len(decided)} test(s)<br>"
            )
            parts.append('<table class="measurements-table">')
            parts.append(
                "<tr><th>Test</th><th>Result</th></tr>"
            )
            for test_name, final_state in sorted(decided.items()):
                tname = html.escape(str(test_name))
                color = LIFECYCLE_COLORS.get(final_state, "#FFFFFF")
                label = LIFECYCLE_LABELS.get(final_state, final_state)
                parts.append(
                    f"<tr><td>{tname}</td>"
                    f'<td><span class="lifecycle-badge" '
                    f'style="background:{color}">{label}</span></td></tr>'
                )
            parts.append("</table>")
        if undecided:
            parts.append(
                f"<strong>Still burning in:</strong> "
                f"{len(undecided)} test(s)<br>"
            )

    parts.append("</div>")
    return "\n".join(parts)


def _render_regression_selection(selection: dict[str, Any]) -> str:
    """Render regression selection details."""
    parts: list[str] = []
    parts.append('<div class="regression-section">')
    parts.append("<h2>Regression Selection</h2>")

    changed = selection.get("changed_files", [])
    if changed:
        parts.append("<details>")
        parts.append(f"<summary>Changed Files ({len(changed)})</summary>")
        parts.append("<ul>")
        for f in changed:
            parts.append(f"<li>{html.escape(str(f))}</li>")
        parts.append("</ul>")
        parts.append("</details>")

    scores = selection.get("scores", {})
    if scores:
        parts.append("<details>")
        parts.append(f"<summary>Test Scores ({len(scores)})</summary>")
        parts.append('<table class="measurements-table">')
        parts.append("<tr><th>Test</th><th>Score</th></tr>")
        for test_name, score in scores.items():
            parts.append(
                f"<tr><td>{html.escape(str(test_name))}</td>"
                f"<td>{score}</td></tr>"
            )
        parts.append("</table>")
        parts.append("</details>")

    parts.append("</div>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# DAG Visualization
# ---------------------------------------------------------------------------

_DAG_CSS = """\
.dag-section {
    background: #fff;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 500px;
}
.dag-section h2 {
    margin: 0 0 12px 0;
    font-size: 18px;
    flex-shrink: 0;
}
.dag-container {
    border: 1px solid #ddd;
    border-radius: 6px;
    overflow: hidden;
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
}
.dag-toolbar {
    display: flex;
    gap: 6px;
    padding: 8px 12px;
    background: #f5f5f5;
    border-bottom: 1px solid #ddd;
    flex-shrink: 0;
}
.dag-toolbar button {
    padding: 4px 12px;
    border: 1px solid #ccc;
    border-radius: 4px;
    background: #fff;
    cursor: pointer;
    font-size: 13px;
}
.dag-toolbar button:hover {
    background: #e8e8e8;
}
.dag-split {
    display: flex;
    flex: 1;
    min-height: 0;
}
.dag-canvas {
    flex: 1;
    min-width: 0;
}
.dag-detail {
    min-width: 400px;
    max-width: 60%;
    border-left: 1px solid #ddd;
    position: relative;
    flex-shrink: 0;
}
.dag-detail-close {
    position: absolute;
    top: 4px;
    right: 8px;
    z-index: 10;
    background: #fff;
    border: 1px solid #ccc;
    border-radius: 50%;
    width: 24px;
    height: 24px;
    cursor: pointer;
    font-size: 14px;
    line-height: 22px;
    text-align: center;
}
.dag-detail-content {
    min-width: max-content;
    height: calc(100% - 8px);
    overflow-y: auto;
    padding: 12px;
    background: #f5f5f5;
    box-sizing: border-box;
}
.dag-resize-handle {
    width: 5px;
    cursor: col-resize;
    background: #ddd;
    flex-shrink: 0;
    transition: background 0.15s;
}
.dag-resize-handle:hover,
.dag-resize-handle.dragging {
    background: #0d6efd;
}
.dag-search-wrapper {
    position: relative;
    margin-left: auto;
}
.dag-search-input {
    padding: 4px 8px;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-size: 13px;
    width: 280px;
    outline: none;
}
.dag-search-input:focus {
    border-color: #0d6efd;
    box-shadow: 0 0 0 2px rgba(13,110,253,0.15);
}
.dag-search-results {
    display: none;
    position: absolute;
    top: 100%;
    right: 0;
    width: 360px;
    max-height: 320px;
    overflow-y: auto;
    background: #fff;
    border: 1px solid #ccc;
    border-radius: 4px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    z-index: 100;
    margin-top: 2px;
}
.dag-search-result {
    padding: 8px 12px;
    cursor: pointer;
    border-bottom: 1px solid #f0f0f0;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.dag-search-result:last-child {
    border-bottom: none;
}
.dag-search-result:hover,
.dag-search-result.active {
    background: #e8f0fe;
}
.dag-search-result-type {
    font-size: 10px;
    font-weight: bold;
    text-transform: uppercase;
    padding: 2px 6px;
    border-radius: 3px;
    flex-shrink: 0;
}
.dag-search-result-type.type-test {
    background: #e8e8e8;
    color: #555;
}
.dag-search-result-type.type-group {
    background: #d4edda;
    color: #155724;
}
.dag-search-result-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.dag-search-no-results {
    padding: 12px;
    color: #999;
    font-size: 13px;
    text-align: center;
}
"""

_DAG_JS = """\
(function() {
    var STATUS_COLORS = {
        success: '#90EE90', failed: '#FFB6C1',
        missing_result: '#FFFFAD', undecided: '#B0C4DE',
        passed: '#90EE90', dependencies_failed: '#D3D3D3',
        'passed+dependencies_failed': '#FFFFAD',
        'failed+dependencies_failed': '#FFB6C1',
        mixed: '#FFFFAD', no_tests: '#D3D3D3', not_run: '#B0C4DE'
    };

    var DAG_COLORS = {green: '#90EE90', red: '#FFB6C1', grey: '#D3D3D3', blue: '#B0C4DE'};

    var LIFECYCLE_ICONS = {
        burning_in: '\\uD83D\\uDD25',
        flaky: '\\u26A0\\uFE0F',
        disabled: '\\uD83D\\uDC7B'
    };

    var LIFECYCLE_BORDER = {
        flaky: '#cf222e',
        burning_in: '#d4a72c',
        disabled: '#999'
    };

    var elements = [];
    var i, d;
    for (i = 0; i < GRAPH_DATA.nodes.length; i++) {
        d = GRAPH_DATA.nodes[i].data;
        elements.push({
            group: 'nodes',
            data: {
                id: d.id, label: d.label, type: d.type,
                status: d.status, lifecycle: d.lifecycle || '',
                dagColor: d.dag_color || 'grey',
                parameters: d.parameters || {}
            },
            classes: d.type
        });
    }
    for (i = 0; i < GRAPH_DATA.edges.length; i++) {
        d = GRAPH_DATA.edges[i].data;
        elements.push({
            group: 'edges',
            data: d,
            classes: d.type || 'dependency'
        });
    }

    var cy = cytoscape({
        container: document.getElementById('dag-canvas'),
        elements: elements,
        style: [
            {
                selector: 'node.group',
                style: {
                    'shape': 'round-rectangle',
                    'corner-radius': 30,
                    'background-color': function(ele) {
                        return DAG_COLORS[ele.data('dagColor')] || '#e8e8e8';
                    },
                    'label': 'data(label)',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'font-size': '12px',
                    'font-weight': 'bold',
                    'width': 'label',
                    'height': '35px',
                    'padding': '12px',
                    'border-width': 2,
                    'border-color': '#888'
                }
            },
            {
                selector: 'node.ci_gate',
                style: {
                    'shape': 'diamond',
                    'background-color': function(ele) {
                        return DAG_COLORS[ele.data('dagColor')] || '#e8e8e8';
                    },
                    'label': 'data(label)',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'font-size': '12px',
                    'font-weight': 'bold',
                    'width': 'label',
                    'height': 'label',
                    'padding': '20px',
                    'border-width': 3,
                    'border-color': '#555'
                }
            },
            {
                selector: 'node.ci_gate[status = "undecided"], node.ci_gate[status = "not_run"]',
                style: {
                    'opacity': 0.6,
                    'border-style': 'dashed'
                }
            },
            {
                selector: 'node.ci_gate:selected',
                style: {
                    'border-width': 4,
                    'border-color': '#0d6efd'
                }
            },
            {
                selector: 'node.test',
                style: {
                    'shape': 'rectangle',
                    'background-color': function(ele) {
                        return STATUS_COLORS[ele.data('status')] || '#e8e8e8';
                    },
                    'label': function(ele) {
                        var icon = LIFECYCLE_ICONS[ele.data('lifecycle')];
                        var lbl = ele.data('label');
                        var prefix = icon ? icon + ' ' : '';
                        var params = ele.data('parameters');
                        if (params && typeof params === 'object') {
                            var keys = Object.keys(params);
                            if (keys.length > 0) {
                                var lines = [prefix + lbl];
                                for (var k = 0; k < keys.length; k++) {
                                    lines.push('  ' + keys[k] + ': ' + params[keys[k]]);
                                }
                                return lines.join('\\n');
                            }
                        }
                        return prefix + lbl;
                    },
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'text-wrap': 'wrap',
                    'text-max-width': '200px',
                    'font-size': '11px',
                    'width': 'label',
                    'height': 'label',
                    'padding': '8px',
                    'border-width': function(ele) {
                        return LIFECYCLE_BORDER[ele.data('lifecycle')] ? 3 : 1;
                    },
                    'border-color': function(ele) {
                        return LIFECYCLE_BORDER[ele.data('lifecycle')] || '#999';
                    }
                }
            },
            {
                selector: 'node.test[status = "undecided"], node.test[status = "not_run"]',
                style: {
                    'opacity': 0.6,
                    'border-style': 'dashed'
                }
            },
            {
                selector: 'node.test:selected',
                style: {
                    'border-width': 3,
                    'border-color': '#0d6efd'
                }
            },
            {
                selector: 'node.group:selected',
                style: {
                    'border-width': 3,
                    'border-color': '#0d6efd'
                }
            },
            {
                selector: 'edge.member',
                style: {
                    'width': 2,
                    'line-color': '#bbb',
                    'target-arrow-color': '#bbb',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'bezier',
                    'arrow-scale': 0.8,
                    'line-style': 'solid'
                }
            },
            {
                selector: 'edge.dependency',
                style: {
                    'width': 2,
                    'line-color': '#666',
                    'target-arrow-color': '#666',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'bezier',
                    'arrow-scale': 0.8,
                    'line-style': 'dashed'
                }
            },
            {
                selector: 'edge.highlighted',
                style: {
                    'width': 3,
                    'line-color': '#0d6efd',
                    'target-arrow-color': '#0d6efd',
                    'z-index': 10
                }
            }
        ],
        layout: {
            name: 'dagre',
            rankDir: 'TB',
            spacingFactor: 1.2,
            nodeSep: 20,
            rankSep: 40
        },
        userZoomingEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
        wheelSensitivity: 0.15,
        minZoom: 0.05
    });

    /* Compute the minimum zoom level so the user cannot zoom out
       beyond the level where the whole graph is visible. */
    var fitZoom = 0.05;
    function updateMinZoom() {
        var prev = cy.zoom();
        var prevPan = {x: cy.pan().x, y: cy.pan().y};
        cy.fit(undefined, 30);
        fitZoom = cy.zoom();
        cy.zoom(prev);
        cy.pan(prevPan);
        cy.minZoom(fitZoom);
    }
    cy.on('layoutstop', updateMinZoom);
    cy.ready(function() { updateMinZoom(); });

    /* Toolbar handlers */
    document.getElementById('dag-zoom-in').addEventListener('click', function() {
        cy.zoom({level: cy.zoom() * 1.2,
            renderedPosition: {x: cy.width()/2, y: cy.height()/2}});
    });
    document.getElementById('dag-zoom-out').addEventListener('click', function() {
        var newLevel = Math.max(cy.zoom() / 1.2, fitZoom);
        cy.zoom({level: newLevel,
            renderedPosition: {x: cy.width()/2, y: cy.height()/2}});
    });
    document.getElementById('dag-fit').addEventListener('click', function() {
        cy.fit(undefined, 30);
    });

    /* Toggle not-run tests visibility */
    var showAllCb = document.getElementById('dag-show-all');
    if (showAllCb) {
        showAllCb.addEventListener('change', function() {
            var show = this.checked;
            cy.batch(function() {
                cy.nodes().forEach(function(node) {
                    if (node.data('status') === 'undecided' || node.data('status') === 'not_run') {
                        node.style('display', show ? 'element' : 'none');
                    }
                });
            });
            cy.layout({
                name: 'dagre', rankDir: 'TB',
                spacingFactor: 1.2, nodeSep: 20, rankSep: 40
            }).run();
        });
    }

    /* Highlight connected edges for a node */
    function highlightNode(node) {
        cy.edges().removeClass('highlighted');
        node.connectedEdges().addClass('highlighted');
    }

    function clearHighlights() {
        cy.edges().removeClass('highlighted');
    }

    /* Resize handle for detail pane */
    var resizeHandle = document.getElementById('dag-resize-handle');
    var detailPane = document.getElementById('dag-detail');
    var splitPane = document.querySelector('.dag-split');
    (function() {
        var dragging = false;
        var startX, startWidth;
        resizeHandle.addEventListener('mousedown', function(e) {
            dragging = true;
            startX = e.clientX;
            startWidth = detailPane.offsetWidth;
            resizeHandle.classList.add('dragging');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            e.preventDefault();
        });
        document.addEventListener('mousemove', function(e) {
            if (!dragging) return;
            var delta = startX - e.clientX;
            var newWidth = Math.max(200, Math.min(startWidth + delta,
                splitPane.offsetWidth - 200));
            detailPane.style.width = newWidth + 'px';
            cy.resize();
        });
        document.addEventListener('mouseup', function() {
            if (!dragging) return;
            dragging = false;
            resizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        });
    })();

    function showDetailPane() {
        detailPane.style.display = 'block';
        resizeHandle.style.display = 'block';
        cy.resize();
    }
    function hideDetailPane() {
        detailPane.style.display = 'none';
        resizeHandle.style.display = 'none';
        cy.resize();
    }

    /* Click test node to show detail pane */
    cy.on('tap', 'node.test', function(evt) {
        highlightNode(evt.target);
        var nodeId = evt.target.data('id');
        var entries = document.querySelectorAll('[data-test-name]');
        var found = null;
        for (var j = 0; j < entries.length; j++) {
            if (entries[j].getAttribute('data-test-name') === nodeId) {
                found = entries[j];
                break;
            }
        }
        if (!found) return;
        var content = document.getElementById('dag-detail-content');
        showDetailPane();
        content.innerHTML = found.outerHTML;
    });

    /* Click ci_gate node to show detail pane */
    cy.on('tap', 'node.ci_gate', function(evt) {
        highlightNode(evt.target);
        var nodeId = evt.target.data('id');
        var entries = document.querySelectorAll('[data-ci-gate-name]');
        var found = null;
        for (var j = 0; j < entries.length; j++) {
            if (entries[j].getAttribute('data-ci-gate-name') === nodeId) {
                found = entries[j];
                break;
            }
        }
        if (!found) return;
        var content = document.getElementById('dag-detail-content');
        showDetailPane();
        var clone = found.cloneNode(true);
        clone.style.display = '';
        content.innerHTML = clone.outerHTML;
    });

    /* Click group (set) node to show detail pane */
    cy.on('tap', 'node.group', function(evt) {
        highlightNode(evt.target);
        var nodeId = evt.target.data('id');
        var entries = document.querySelectorAll('[data-set-name]');
        var found = null;
        for (var j = 0; j < entries.length; j++) {
            if (entries[j].getAttribute('data-set-name') === nodeId) {
                found = entries[j];
                break;
            }
        }
        if (!found) return;
        var content = document.getElementById('dag-detail-content');
        showDetailPane();
        var clone = found.cloneNode(true);
        clone.style.display = '';
        content.innerHTML = clone.outerHTML;
    });

    /* Click background to clear selection and highlights */
    cy.on('tap', function(evt) {
        if (evt.target === cy) {
            clearHighlights();
        }
    });

    document.getElementById('dag-detail-close').addEventListener('click',
        function() {
            hideDetailPane();
            clearHighlights();
            cy.elements().unselect();
        });

    /* ----- Search ----- */
    var searchInput = document.getElementById('dag-search');
    var searchResults = document.getElementById('dag-search-results');
    var searchTimer = null;
    var activeResultIdx = -1;
    var VALID_FIELDS = {
        name:1, assertion:1, parameter:1, metric:1, check:1, feature:1, log:1
    };
    var SCOPE_RE = /(\\w+):\\(([^)]*)\\)/g;

    function parseQuery(raw) {
        var scoped = [];
        var remainder = raw.replace(SCOPE_RE, function(_, field, value) {
            var f = field.toLowerCase();
            if (VALID_FIELDS[f]) {
                scoped.push({field: f, keyword: value.trim().toLowerCase()});
            }
            return '';
        });
        var unscoped = [];
        var parts = remainder.trim().split(/\\s+/);
        for (var i = 0; i < parts.length; i++) {
            if (parts[i]) unscoped.push(parts[i].toLowerCase());
        }
        return {scoped: scoped, unscoped: unscoped};
    }

    function performSearch(query) {
        var parsed = parseQuery(query);
        searchResults.innerHTML = '';
        activeResultIdx = -1;

        if (parsed.scoped.length === 0 && parsed.unscoped.length === 0) {
            searchResults.style.display = 'none';
            return;
        }
        if (parsed.scoped.length === 0 &&
            parsed.unscoped.length === 1 &&
            parsed.unscoped[0].length < 2) {
            searchResults.style.display = 'none';
            return;
        }

        var matches = [];
        var ids = Object.keys(SEARCH_INDEX);

        for (var i = 0; i < ids.length; i++) {
            var entry = SEARCH_INDEX[ids[i]];
            var fields = entry.fields;
            var allMatch = true;

            /* Check scoped terms: each must match its specific field */
            for (var s = 0; s < parsed.scoped.length; s++) {
                var sf = parsed.scoped[s].field;
                var sk = parsed.scoped[s].keyword;
                var fieldText = fields[sf] || '';
                if (fieldText.indexOf(sk) === -1) {
                    allMatch = false;
                    break;
                }
            }
            if (!allMatch) continue;

            /* Check unscoped terms: each must match in ANY field */
            if (parsed.unscoped.length > 0) {
                var allFields = '';
                var fkeys = Object.keys(fields);
                for (var fk = 0; fk < fkeys.length; fk++) {
                    allFields += fields[fkeys[fk]] + ' ';
                }
                for (var u = 0; u < parsed.unscoped.length; u++) {
                    if (allFields.indexOf(parsed.unscoped[u]) === -1) {
                        allMatch = false;
                        break;
                    }
                }
            }

            if (allMatch) {
                matches.push({
                    id: ids[i], type: entry.type, label: entry.label
                });
            }
            if (matches.length >= 20) break;
        }

        if (matches.length === 0) {
            searchResults.innerHTML =
                '<div class="dag-search-no-results">No results</div>';
            searchResults.style.display = 'block';
            return;
        }

        for (var m = 0; m < matches.length; m++) {
            var div = document.createElement('div');
            div.className = 'dag-search-result';
            div.setAttribute('data-node-id', matches[m].id);
            div.setAttribute('data-node-type', matches[m].type);

            var typeBadge = document.createElement('span');
            typeBadge.className =
                'dag-search-result-type type-' + matches[m].type;
            typeBadge.textContent =
                matches[m].type === 'test' ? 'TEST' : 'SET';

            var nameSpan = document.createElement('span');
            nameSpan.className = 'dag-search-result-name';
            nameSpan.textContent = matches[m].label;

            div.appendChild(typeBadge);
            div.appendChild(nameSpan);
            div.addEventListener('click', (function(nodeId, nodeType) {
                return function() {
                    selectSearchResult(nodeId, nodeType);
                };
            })(matches[m].id, matches[m].type));
            searchResults.appendChild(div);
        }

        searchResults.style.display = 'block';
    }

    function selectSearchResult(nodeId, nodeType) {
        searchResults.style.display = 'none';

        var node = cy.getElementById(nodeId);
        if (node.length === 0) return;

        /* Make node visible if hidden (e.g. not_run with toggle off) */
        if (node.style('display') === 'none') {
            node.style('display', 'element');
        }

        cy.elements().unselect();
        node.select();
        highlightNode(node);

        cy.animate({
            center: {eles: node},
            zoom: Math.max(cy.zoom(), 1.0)
        }, {duration: 300});

        /* Open detail pane (mirrors tap handler logic) */
        var content = document.getElementById('dag-detail-content');
        var selector, attr;
        if (nodeType === 'test') {
            attr = 'data-test-name';
        } else {
            attr = 'data-set-name';
        }
        var entries = document.querySelectorAll('[' + attr + ']');
        for (var j = 0; j < entries.length; j++) {
            if (entries[j].getAttribute(attr) === nodeId) {
                showDetailPane();
                var el = entries[j];
                if (nodeType === 'group') {
                    var clone = el.cloneNode(true);
                    clone.style.display = '';
                    content.innerHTML = clone.outerHTML;
                } else {
                    content.innerHTML = el.outerHTML;
                }
                break;
            }
        }
    }

    /* Debounced input handler (250ms) */
    searchInput.addEventListener('input', function() {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function() {
            performSearch(searchInput.value);
        }, 250);
    });

    /* Keyboard navigation */
    searchInput.addEventListener('keydown', function(e) {
        var items = searchResults.querySelectorAll('.dag-search-result');
        if (e.key === 'Escape') {
            searchResults.style.display = 'none';
            searchInput.blur();
            return;
        }
        if (items.length === 0) return;
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            activeResultIdx = Math.min(
                activeResultIdx + 1, items.length - 1
            );
            updateActiveResult(items);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            activeResultIdx = Math.max(activeResultIdx - 1, 0);
            updateActiveResult(items);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (activeResultIdx >= 0 && activeResultIdx < items.length) {
                var item = items[activeResultIdx];
                selectSearchResult(
                    item.getAttribute('data-node-id'),
                    item.getAttribute('data-node-type')
                );
            }
        }
    });

    function updateActiveResult(items) {
        for (var i = 0; i < items.length; i++) {
            items[i].classList.toggle('active', i === activeResultIdx);
        }
        if (activeResultIdx >= 0 && items[activeResultIdx]) {
            items[activeResultIdx].scrollIntoView({block: 'nearest'});
        }
    }

    /* Click outside to close dropdown */
    document.addEventListener('click', function(e) {
        if (!searchInput.contains(e.target) &&
            !searchResults.contains(e.target)) {
            searchResults.style.display = 'none';
        }
    });

    /* Re-open on focus if query present */
    searchInput.addEventListener('focus', function() {
        if (searchInput.value.trim().length > 0) {
            performSearch(searchInput.value);
        }
    });
})();
"""


# Map verdict state to a DAG display color (verdict + backward-compat).
_STATUS_DAG_COLOR: dict[str, str] = {
    # Verdict states
    "success": "green",
    "failed": "red",
    "missing_result": "grey",
    "undecided": "blue",
    # Backward-compat aliases for old reports
    "passed": "green",
    "dependencies_failed": "grey",
    "passed+dependencies_failed": "green",
    "failed+dependencies_failed": "red",
    "mixed": "red",
    "no_tests": "grey",
    "not_run": "blue",
}


def _build_graph_data(test_set: dict[str, Any]) -> dict[str, Any]:
    """Build Cytoscape.js-compatible graph data from the test_set hierarchy.

    Nodes are **not** nested via Cytoscape compound parents.  Instead,
    membership (test-set → test, test-set → sub-set) is expressed as
    regular edges so that the same node can appear as a member of
    multiple test sets (DAG, not tree).

    After walking, a leaf-to-root pass computes ``dag_color`` for every
    node: test nodes derive it from their run status; group nodes
    aggregate from their direct member children (red if any child red,
    grey if all children grey, green otherwise).

    Returns:
        Dict with ``nodes`` and ``edges`` lists in Cytoscape elements format.
    """
    seen_nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    _walk_dag_for_graph(test_set, parent_id=None,
                        seen_nodes=seen_nodes, edges=edges)

    # Remove synthetic "Workspace" root node — its children become
    # top-level roots in the DAG (which may be disconnected).
    ws_name = "Workspace"
    if ws_name in seen_nodes:
        del seen_nodes[ws_name]
        edges = [e for e in edges
                 if e["data"]["source"] != ws_name
                 and e["data"]["target"] != ws_name]

    # Assign dag_color from each node's own status.  The status was already
    # correctly computed by _aggregate_status() using four-state priority
    # aggregation, so we just map it to a display color.  We intentionally
    # do NOT aggregate colors from children because shared nodes
    # (deduplicated by _walk_dag_for_graph) may carry a status from a
    # different parent context.
    for node in seen_nodes.values():
        data = node["data"]
        data["dag_color"] = _STATUS_DAG_COLOR.get(data["status"], "grey")

    return {"nodes": list(seen_nodes.values()), "edges": edges}


def _walk_dag_for_graph(
    node: dict[str, Any],
    parent_id: str | None,
    seen_nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Recursively walk the test_set tree and populate graph elements.

    Nodes are deduplicated so a test or subset appearing under multiple
    parents is emitted only once.  Membership is expressed as edges
    from the parent test-set node to its children.
    """
    node_id = node.get("name", "")
    node_type = "ci_gate" if "ci_gate_params" in node else "group"
    first_visit = node_id not in seen_nodes
    if first_visit:
        seen_nodes[node_id] = {"data": {
            "id": node_id,
            "label": node_id,
            "type": node_type,
            "status": node.get("status", "success"),
        }}

    if parent_id is not None:
        edges.append({"data": {
            "source": parent_id, "target": node_id, "type": "member",
        }})

    # If this node was already walked, its children (tests & subsets)
    # have already been emitted — skip to avoid duplicate edges.
    if not first_visit:
        return

    for test_name, test_data in node.get("tests", {}).items():
        if test_name not in seen_nodes:
            short_label = (
                test_name.rsplit(":", 1)[-1] if ":" in test_name else test_name
            )
            lifecycle = test_data.get("lifecycle") or {}
            parameters = test_data.get("parameters") or {}
            seen_nodes[test_name] = {"data": {
                "id": test_name,
                "label": short_label,
                "type": "test",
                "status": test_data.get("status", "success"),
                "lifecycle": lifecycle.get("state", ""),
                "parameters": parameters,
            }}
        edges.append({"data": {
            "source": node_id, "target": test_name, "type": "member",
        }})
        for dep in test_data.get("depends_on", []):
            edges.append({"data": {
                "source": test_name, "target": dep, "type": "dependency",
            }})

    for subset in node.get("subsets", []):
        _walk_dag_for_graph(
            subset, parent_id=node_id,
            seen_nodes=seen_nodes, edges=edges,
        )


# ---------------------------------------------------------------------------
# Search Index
# ---------------------------------------------------------------------------

# Regex to split camelCase identifiers at word boundaries.
# Splits before an uppercase letter preceded by a lowercase letter,
# and between consecutive uppercase letters followed by a lowercase letter
# (e.g. "myHTTPClient" → "my", "HTTP", "Client").
_CAMEL_SPLIT_RE = re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def _decompose_identifier(text: str) -> str:
    """Decompose camelCase and snake_case identifiers into words.

    Returns the original text plus the decomposed words joined by spaces.

    Examples:
        ``"processPayment"`` → ``"processPayment process payment"``
        ``"process_payment"`` → ``"process_payment process payment"``
        ``"myHTTPClient"`` → ``"myHTTPClient my HTTP Client"``
    """
    parts = text.split("_")
    words: list[str] = []
    for part in parts:
        split = _CAMEL_SPLIT_RE.split(part)
        words.extend(w for w in split if w)
    decomposed = " ".join(words)
    if decomposed == text:
        return text
    return f"{text} {decomposed}"


def _collect_step_search_text(
    step: StepSegment,
    features: list[str],
    metrics: list[str],
    checks: list[str],
) -> None:
    """Extract searchable text from a step and its sub-steps."""
    features.append(_decompose_identifier(step.step))
    if step.description:
        features.append(_decompose_identifier(step.description))
    for f in step.features:
        name = f.get("name", "")
        if name:
            features.append(_decompose_identifier(name))
    for m in step.measurements:
        metrics.append(_decompose_identifier(str(m.get("name", ""))))
        metrics.append(str(m.get("value", "")))
        unit = m.get("unit")
        if unit:
            metrics.append(str(unit))
    for a in step.assertions:
        checks.append(_decompose_identifier(str(a.get("description", ""))))
    for sub in step.steps:
        _collect_step_search_text(sub, features, metrics, checks)


def _collect_block_search_text(
    block: BlockSegment,
) -> tuple[str, str, str]:
    """Extract searchable text from a block and its steps.

    Returns (features_text, metrics_text, checks_text).
    """
    features: list[str] = []
    metrics: list[str] = []
    checks: list[str] = []
    if block.description:
        features.append(_decompose_identifier(block.description))
    for f in block.features:
        name = f.get("name", "")
        if name:
            features.append(_decompose_identifier(name))
    for m in block.measurements:
        metrics.append(_decompose_identifier(str(m.get("name", ""))))
        metrics.append(str(m.get("value", "")))
        unit = m.get("unit")
        if unit:
            metrics.append(str(unit))
    for a in block.assertions:
        checks.append(_decompose_identifier(str(a.get("description", ""))))
    for step in block.steps:
        _collect_step_search_text(step, features, metrics, checks)
    return " ".join(features), " ".join(metrics), " ".join(checks)


def _walk_for_search_index(
    test_set: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> None:
    """Recursively build search index entries for sets and tests."""
    name = test_set.get("name", "")
    node_type = "ci_gate" if "ci_gate_params" in test_set else "group"
    if name and name != "Workspace":
        name_parts = [_decompose_identifier(name)]
        assertion = test_set.get("assertion", "")
        index[name] = {
            "type": node_type,
            "label": name,
            "fields": {
                "name": " ".join(name_parts).lower(),
                "assertion": _decompose_identifier(assertion).lower(),
            },
        }

    for test_name, test_data in test_set.get("tests", {}).items():
        if test_name in index:
            continue

        name_text = _decompose_identifier(test_name)
        assertion = test_data.get("assertion", "")
        assertion_text = _decompose_identifier(assertion) if assertion else ""

        param_parts: list[str] = []
        for k, v in (test_data.get("parameters") or {}).items():
            param_parts.append(_decompose_identifier(str(k)))
            param_parts.append(_decompose_identifier(str(v)))

        feature_parts: list[str] = []
        metric_parts: list[str] = []
        check_parts: list[str] = []
        stdout = test_data.get("stdout", "")
        if stdout:
            segments = parse_stdout_segments(stdout)
            for seg in segments:
                if isinstance(seg, BlockSegment):
                    ft, mt, ct = _collect_block_search_text(seg)
                    if ft:
                        feature_parts.append(ft)
                    if mt:
                        metric_parts.append(mt)
                    if ct:
                        check_parts.append(ct)

        log_text = stdout[:5000] if stdout else ""

        short_label = (
            test_name.rsplit(":", 1)[-1] if ":" in test_name
            else test_name
        )
        index[test_name] = {
            "type": "test",
            "label": short_label,
            "fields": {
                "name": name_text.lower(),
                "assertion": assertion_text.lower(),
                "parameter": " ".join(param_parts).lower(),
                "metric": " ".join(metric_parts).lower(),
                "check": " ".join(check_parts).lower(),
                "feature": " ".join(feature_parts).lower(),
                "log": log_text.lower(),
            },
        }

    for subset in test_set.get("subsets", []):
        _walk_for_search_index(subset, index)


def _build_search_index(
    test_set: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build a search index mapping node IDs to searchable text fields.

    Returns a dict keyed by node ID with values containing ``type``
    (``"test"`` or ``"group"``), ``label`` (display name), and
    ``fields`` (dict of field name → lowercased searchable text).
    """
    index: dict[str, dict[str, Any]] = {}
    _walk_for_search_index(test_set, index)
    return index


def _render_dag_data_elements(
    test_set: dict[str, Any],
    history: dict[str, list[dict[str, Any]]],
    lifecycle_config: dict[str, Any] | None = None,
    source_link_base: str | None = None,
    *,
    e_value_verdict: dict[str, Any] | None = None,
    effort_data: dict[str, Any] | None = None,
    ci_gate_name: str | None = None,
) -> str:
    """Render hidden data elements for the DAG detail pane.

    Produces hidden test-entry divs (``data-test-name``), set
    summary cards (``data-set-name``), and ci_gate cards
    (``data-ci-gate-name``) so that the Cytoscape tap handlers
    can clone them into the detail pane.
    """
    parts: list[str] = []
    parts.append('<div style="display:none">')
    _walk_for_data_elements(
        test_set, history, lifecycle_config, source_link_base, parts,
        e_value_verdict=e_value_verdict,
        effort_data=effort_data,
        ci_gate_name=ci_gate_name,
    )
    parts.append("</div>")
    return "\n".join(parts)


def _walk_for_data_elements(
    test_set: dict[str, Any],
    history: dict[str, list[dict[str, Any]]],
    lifecycle_config: dict[str, Any] | None,
    source_link_base: str | None,
    parts: list[str],
    *,
    e_value_verdict: dict[str, Any] | None = None,
    effort_data: dict[str, Any] | None = None,
    ci_gate_name: str | None = None,
) -> None:
    """Recursively collect hidden data elements for every set, test, and ci_gate."""
    # CI gate card
    if "ci_gate_params" in test_set:
        parts.append(_render_ci_gate_card(
            test_set,
            e_value_verdict=e_value_verdict,
            effort_data=effort_data,
            ci_gate_name=ci_gate_name,
        ))
    else:
        # Set summary card (with inline e-value/effort for executing gate's test_set)
        set_test_names = _collect_test_names(test_set)
        set_history = _compute_set_history(set_test_names, history)
        parts.append(_render_set_summary_card(
            test_set, lifecycle_config, set_history,
            e_value_verdict=e_value_verdict,
            effort_data=effort_data,
            ci_gate_name=ci_gate_name,
        ))

    # Individual test entries
    effort_classifications = (
        effort_data.get("classifications", {}) if effort_data else {}
    )
    ev_per_test = {}
    if e_value_verdict:
        for tv in e_value_verdict.get("per_test", []):
            ev_per_test[tv.get("test_name", "")] = tv

    for test_name, test_data in test_set.get("tests", {}).items():
        parts.append(_render_test_entry(
            test_name, test_data, history.get(test_name, []),
            source_link_base=source_link_base,
            effort_classification=effort_classifications.get(test_name),
            e_value_per_test=ev_per_test.get(test_name),
        ))

    # Recurse into subsets
    for subset in test_set.get("subsets", []):
        _walk_for_data_elements(
            subset, history, lifecycle_config, source_link_base, parts,
            e_value_verdict=e_value_verdict,
            effort_data=effort_data,
            ci_gate_name=ci_gate_name,
        )


def _render_dag_section(
    report: dict[str, Any],
    history: dict[str, list[dict[str, Any]]] | None = None,
    lifecycle_config: dict[str, Any] | None = None,
    source_link_base: str | None = None,
) -> str:
    """Render the interactive DAG visualization section."""
    test_set = report.get("test_set", {})
    graph_data = _build_graph_data(test_set)
    search_index = _build_search_index(test_set)

    # Use StatusFile history when available (Step 7) — it contains the
    # full run history that e-values are computed from.
    sf_history = report.get("status_file_history")
    if sf_history:
        history = sf_history

    parts: list[str] = []
    parts.append(f"<style>{_DAG_CSS}</style>")
    parts.append('<div class="dag-section">')
    parts.append("<h2>Test DAG</h2>")
    parts.append('<div class="dag-container">')

    # Toolbar
    parts.append('<div class="dag-toolbar">')
    parts.append('<button id="dag-zoom-in" title="Zoom in">+</button>')
    parts.append(
        '<button id="dag-zoom-out" title="Zoom out">&minus;</button>'
    )
    parts.append('<button id="dag-fit" title="Fit to view">Fit</button>')
    parts.append(
        '<label style="margin-left:12px;font-size:13px;display:flex;'
        'align-items:center;gap:4px;cursor:pointer">'
        '<input type="checkbox" id="dag-show-all" checked>'
        'Show all workspace tests</label>'
    )
    parts.append(
        '<div class="dag-search-wrapper">'
        '<input type="text" id="dag-search" class="dag-search-input"'
        ' placeholder="Search tests...'
        ' e.g. name:(email) check:(SMTP)" autocomplete="off">'
        '<div id="dag-search-results" class="dag-search-results"></div>'
        '</div>'
    )
    parts.append("</div>")

    # Split pane: canvas + detail
    parts.append('<div class="dag-split">')
    parts.append('<div id="dag-canvas" class="dag-canvas"></div>')
    parts.append(
        '<div id="dag-resize-handle" class="dag-resize-handle"'
        ' style="display:none"></div>'
    )
    parts.append(
        '<div id="dag-detail" class="dag-detail" style="display:none">'
    )
    parts.append(
        '<button id="dag-detail-close" class="dag-detail-close"'
        ' title="Close">&times;</button>'
    )
    parts.append(
        '<div id="dag-detail-content" class="dag-detail-content"></div>'
    )
    parts.append("</div>")  # dag-detail
    parts.append("</div>")  # dag-split
    parts.append("</div>")  # dag-container
    parts.append("</div>")  # dag-section

    # Hidden data elements for detail pane (test entries + set summaries + ci_gates)
    parts.append(_render_dag_data_elements(
        test_set, history or {},
        lifecycle_config=lifecycle_config,
        source_link_base=source_link_base,
        e_value_verdict=report.get("e_value_verdict"),
        effort_data=report.get("effort"),
        ci_gate_name=report.get("ci_gate_name"),
    ))

    # Embedded data
    graph_json = json.dumps(graph_data, separators=(",", ":"))
    parts.append(f"<script>var GRAPH_DATA={graph_json};</script>")
    search_json = json.dumps(search_index, separators=(",", ":"))
    parts.append(f"<script>var SEARCH_INDEX={search_json};</script>")

    # CDN libraries
    parts.append(
        '<script src="https://unpkg.com/cytoscape@3.30.4/dist/'
        'cytoscape.min.js"></script>'
    )
    parts.append(
        '<script src="https://unpkg.com/dagre@0.8.5/dist/'
        'dagre.min.js"></script>'
    )
    parts.append(
        '<script src="https://unpkg.com/cytoscape-dagre@2.5.0/'
        'cytoscape-dagre.js"></script>'
    )

    # Application JavaScript
    parts.append(f"<script>{_DAG_JS}</script>")

    return "\n".join(parts)
