"""HTML report generation from JSON test reports.

Generates self-contained HTML reports with color-coded statuses,
expandable log and measurement sections, and visual DAG structure.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from orchestrator.analysis.log_parser import (
    BlockSegment,
    TextSegment,
    parse_stdout_segments,
)


# Status color mapping
STATUS_COLORS: dict[str, str] = {
    "passed": "#90EE90",
    "failed": "#FFB6C1",
    "dependencies_failed": "#D3D3D3",
    "passed+dependencies_failed": "#FFFFAD",
    "failed+dependencies_failed": "#FFB6C1",
    "mixed": "#FFFFAD",
    "no_tests": "#D3D3D3",
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

# Status display labels
STATUS_LABELS: dict[str, str] = {
    "passed": "PASSED",
    "failed": "FAILED",
    "dependencies_failed": "DEPS FAILED",
    "passed+dependencies_failed": "PASSED (deps failed)",
    "failed+dependencies_failed": "FAILED (deps failed)",
    "mixed": "MIXED",
    "no_tests": "NO TESTS",
}

_CSS = """\
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    margin: 0;
    padding: 20px;
    background: #f5f5f5;
    color: #333;
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
.test-set {
    background: #fff;
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
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
.test-list {
    background: #f5f5f5;
    border-radius: 6px;
    padding: 8px 12px;
    margin-top: 8px;
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
details.test-set-details {
    margin: 10px 0 10px 12px;
}
details.test-set-details > summary {
    cursor: pointer;
    font-size: 15px;
    font-weight: 600;
    color: #333;
    list-style: none;
    padding: 8px 0;
}
details.test-set-details > summary::-webkit-details-marker {
    display: none;
}
details.test-set-details > summary::before {
    content: '\\25B6';
    display: inline-block;
    margin-right: 8px;
    font-size: 11px;
    transition: transform 0.15s;
}
details.test-set-details[open] > summary::before {
    transform: rotate(90deg);
}
details.test-set-details > .test-set-nested {
    background: #fff;
    border-radius: 8px;
    padding: 12px 16px;
    margin-top: 4px;
    border-left: 3px solid #ddd;
    box-shadow: 0 1px 2px rgba(0,0,0,0.06);
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
    gap: 1px;
    margin: 6px 0;
    overflow: hidden;
    max-width: 100%;
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

    # Test set (hierarchical) or flat tests
    if "test_set" in report:
        parts.append(_render_test_set(
            report["test_set"], history,
            lifecycle_config=lifecycle_config,
        ))
    elif "tests" in report:
        parts.append(_render_flat_tests(report["tests"], history))

    # E-value verdict section
    if "e_value_verdict" in report:
        parts.append(_render_e_value_verdict(report["e_value_verdict"]))

    # Effort classification section
    if "effort" in report:
        parts.append(_render_effort_section(report["effort"]))

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

    summary = report.get("summary", {})
    if summary:
        parts.append('<div class="summary">')
        total = summary.get("total", 0)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        dep_failed = summary.get("dependencies_failed", 0)
        duration = summary.get("total_duration_seconds", 0)

        parts.append(
            f'<div class="summary-item" style="background:#e8e8e8">'
            f"Total: {total}</div>"
        )
        if passed:
            parts.append(
                f'<div class="summary-item" style="background:#90EE90">'
                f"Passed: {passed}</div>"
            )
        if failed:
            parts.append(
                f'<div class="summary-item" style="background:#FFB6C1">'
                f"Failed: {failed}</div>"
            )
        if dep_failed:
            parts.append(
                f'<div class="summary-item" style="background:#D3D3D3">'
                f"Deps Failed: {dep_failed}</div>"
            )
        parts.append(
            f'<div class="summary-item" style="background:#e8e8e8">'
            f"Duration: {duration:.3f}s</div>"
        )
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


def _has_descendant_failure(test_set: dict[str, Any]) -> bool:
    """Return True if any test or nested subset has a failure."""
    status = test_set.get("status", "")
    if "failed" in status:
        return True
    for test_data in test_set.get("tests", {}).values():
        if "failed" in test_data.get("status", ""):
            return True
    for subset in test_set.get("subsets", []):
        if _has_descendant_failure(subset):
            return True
    return False


def _render_test_set(
    test_set: dict[str, Any],
    history: dict[str, list[dict[str, Any]]],
    is_root: bool = True,
    lifecycle_config: dict[str, Any] | None = None,
) -> str:
    """Render a test set section, recursing into subsets.

    Root renders as a plain ``<div>``; nested sets render inside
    collapsible ``<details>`` elements (collapsed by default,
    auto-expanded when they contain a failure).
    """
    parts: list[str] = []
    name = test_set.get("name", "Test Set")
    status = test_set.get("status", "no_tests")
    assertion = test_set.get("assertion", "")
    color = STATUS_COLORS.get(status, "#e8e8e8")
    label = STATUS_LABELS.get(status, status.upper())

    badge = (
        f'<span class="status-badge" style="background:{color}">'
        f"{html.escape(label)}</span>"
    )

    if is_root:
        # Root: always-visible card
        parts.append('<div class="test-set">')
        parts.append('<div class="test-set-header">')
        parts.append(f"<h2>{html.escape(name)}</h2>")
        parts.append(badge)
        parts.append("</div>")
        if assertion:
            parts.append(
                f'<div class="test-meta">Assertion: {html.escape(assertion)}</div>'
            )
    else:
        # Nested: collapsible <details>
        open_attr = " open" if _has_descendant_failure(test_set) else ""
        parts.append(f'<details class="test-set-details"{open_attr}>')
        parts.append(
            f"<summary>{html.escape(name)} {badge}</summary>"
        )
        parts.append('<div class="test-set-nested">')
        if assertion:
            parts.append(
                f'<div class="test-meta">Assertion: {html.escape(assertion)}</div>'
            )

    # Lifecycle summary for this test set
    lifecycle_summary = test_set.get("lifecycle_summary")
    if lifecycle_summary:
        parts.append(_render_lifecycle_summary(
            lifecycle_summary, lifecycle_config,
        ))

    # Direct tests inside a test-list container
    tests = test_set.get("tests", {})
    if tests:
        parts.append('<div class="test-list">')
        for test_name, test_data in tests.items():
            parts.append(
                _render_test_entry(test_name, test_data, history.get(test_name, []))
            )
        parts.append("</div>")

    # Recurse into subsets
    for subset in test_set.get("subsets", []):
        parts.append(_render_test_set(
            subset, history, is_root=False,
            lifecycle_config=lifecycle_config,
        ))

    # Close wrappers
    if is_root:
        parts.append("</div>")
    else:
        parts.append("</div>")   # .test-set-nested
        parts.append("</details>")

    return "\n".join(parts)


def _render_test_entry(
    name: str, data: dict[str, Any],
    history_entries: list[dict[str, Any]] | None = None,
) -> str:
    """Render a single test entry with expandable details."""
    parts: list[str] = []
    status = data.get("status", "no_tests")
    color = STATUS_COLORS.get(status, "#e8e8e8")
    label = STATUS_LABELS.get(status, status.upper())
    duration = data.get("duration_seconds", 0)
    assertion = data.get("assertion", "")

    parts.append(f'<div class="test-entry" style="border-left-color:{color}">')

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
        parts.append('<details class="log-details">')
        parts.append("<summary>Logs</summary>")
        if stdout:
            segments = parse_stdout_segments(stdout)
            has_blocks = any(isinstance(s, BlockSegment) for s in segments)
            if has_blocks:
                parts.append(_render_stdout_segments(segments))
            else:
                parts.append(f"<pre>{html.escape(stdout)}</pre>")
        if stderr:
            parts.append(f'<pre style="border-left:3px solid #FFB6C1">'
                         f"{html.escape(stderr)}</pre>")
        parts.append("</details>")

    # Burn-in progress
    burn_in = data.get("burn_in")
    if burn_in:
        parts.append(_render_burn_in(burn_in))

    # Inferred dependencies
    inferred_deps = data.get("inferred_dependencies")
    if inferred_deps:
        parts.append(_render_inferred_deps(inferred_deps))

    parts.append("</div>")
    return "\n".join(parts)


def _render_stdout_segments(segments: list[TextSegment | BlockSegment]) -> str:
    """Render parsed stdout segments as unified HTML."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSegment):
            text = seg.text.strip()
            if text:
                parts.append(f"<pre>{html.escape(text)}</pre>")
        elif isinstance(seg, BlockSegment):
            parts.append(_render_block_segment(seg))
    return "\n".join(parts)


def _render_block_segment(block: BlockSegment) -> str:
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

    # Features
    if block.features:
        feat_strs = [html.escape(f.get("name", "")) for f in block.features]
        parts.append(
            f'<div class="block-features">Features: {", ".join(feat_strs)}</div>'
        )

    # Measurements table
    if block.measurements:
        parts.append('<table class="measurements-table">')
        parts.append("<tr><th>Name</th><th>Value</th><th>Unit</th></tr>")
        for m in block.measurements:
            mname = html.escape(str(m.get("name", "")))
            mval = html.escape(str(m.get("value", "")))
            munit = html.escape(str(m.get("unit", "")))
            parts.append(
                f"<tr><td>{mname}</td><td>{mval}</td><td>{munit}</td></tr>"
            )
        parts.append("</table>")

    # Assertions
    if block.assertions:
        parts.append('<ul class="assertion-list">')
        for a in block.assertions:
            desc = html.escape(str(a.get("description", "")))
            status = a.get("status", "unknown")
            css_class = "assertion-pass" if status == "passed" else "assertion-fail"
            parts.append(f'<li class="{css_class}">{desc}</li>')
        parts.append("</ul>")

    # Block logs (plain text emitted during the block)
    if block.logs:
        parts.append(f"<pre>{html.escape(block.logs)}</pre>")

    # Error
    if block.error:
        parts.append(
            f'<div class="block-error">Error: {html.escape(block.error)}</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)


# History timeline status-to-color mapping
_TIMELINE_COLORS: dict[str, str] = {
    "passed": "#2da44e",
    "failed": "#cf222e",
    "dependencies_failed": "#999",
    "passed+dependencies_failed": "#d4a72c",
    "failed+dependencies_failed": "#cf222e",
    "mixed": "#d4a72c",
    "no_tests": "#999",
}


def _render_history_timeline(entries: list[dict[str, Any]]) -> str:
    """Render a compact horizontal pass/fail history timeline.

    Each entry becomes a small colored box. Hovering shows the commit hash.
    Entries are displayed in chronological order (oldest left, newest right).
    """
    if not entries:
        return ""
    parts: list[str] = []
    parts.append('<div class="history-timeline">')
    for entry in entries:
        status = entry.get("status", "no_tests")
        color = _TIMELINE_COLORS.get(status, "#999")
        commit = entry.get("commit", "")
        tooltip = html.escape(commit[:12]) if commit else html.escape(status)
        parts.append(
            f'<div class="ht-box" style="background:{color}" '
            f'title="{tooltip}"></div>'
        )
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
    parts.append('<details class="log-details">')
    parts.append("<summary>Inferred Dependencies</summary>")
    parts.append("<ul>")
    for dep in deps:
        dep_str = html.escape(str(dep.get("name", dep)))
        parts.append(f"<li>{dep_str}</li>")
    parts.append("</ul>")
    parts.append("</details>")
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


def _render_flat_tests(
    tests: list[dict[str, Any]], history: dict[str, list[dict[str, Any]]]
) -> str:
    """Render a flat (non-hierarchical) test list."""
    parts: list[str] = []
    parts.append('<div class="flat-tests">')
    parts.append("<h2>Test Results</h2>")
    for test in tests:
        name = test.get("name", "unknown")
        parts.append(_render_test_entry(name, test, history.get(name, [])))
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
