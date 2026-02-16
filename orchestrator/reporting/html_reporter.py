"""HTML report generation from JSON test reports.

Generates self-contained HTML reports with color-coded statuses,
expandable log and measurement sections, and visual DAG structure.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


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
.test-set-header h2 {
    margin: 0;
    font-size: 18px;
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
    margin: 8px 0 8px 16px;
    padding: 8px 12px;
    background: #fafafa;
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
details {
    margin-top: 8px;
}
summary {
    cursor: pointer;
    font-size: 13px;
    color: #555;
    font-weight: 500;
}
summary:hover {
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

    # Test set (hierarchical) or flat tests
    if "test_set" in report:
        parts.append(_render_test_set(report["test_set"], history))
    elif "tests" in report:
        parts.append(_render_flat_tests(report["tests"], history))

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


def _render_test_set(
    test_set: dict[str, Any], history: dict[str, list[dict[str, Any]]]
) -> str:
    """Render a hierarchical test set section."""
    parts: list[str] = []
    name = test_set.get("name", "Test Set")
    status = test_set.get("status", "no_tests")
    assertion = test_set.get("assertion", "")
    color = STATUS_COLORS.get(status, "#e8e8e8")
    label = STATUS_LABELS.get(status, status.upper())

    parts.append('<div class="test-set">')
    parts.append('<div class="test-set-header">')
    parts.append(f"<h2>{html.escape(name)}</h2>")
    parts.append(
        f'<span class="status-badge" style="background:{color}">'
        f"{html.escape(label)}</span>"
    )
    parts.append("</div>")

    if assertion:
        parts.append(f'<div class="test-meta">Assertion: {html.escape(assertion)}</div>')

    tests = test_set.get("tests", {})
    for test_name, test_data in tests.items():
        parts.append(_render_test_entry(test_name, test_data, history.get(test_name, [])))

    parts.append("</div>")
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
    parts.append(
        f'<div class="test-name">{html.escape(name)} '
        f'<span class="status-badge" style="background:{color}">'
        f"{html.escape(label)}</span></div>"
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
        parts.append("<details>")
        parts.append("<summary>Logs</summary>")
        if stdout:
            parts.append(f"<pre>{html.escape(stdout)}</pre>")
        if stderr:
            parts.append(f'<pre style="border-left:3px solid #FFB6C1">'
                         f"{html.escape(stderr)}</pre>")
        parts.append("</details>")

    # Structured log data
    structured_log = data.get("structured_log")
    if structured_log:
        parts.append(_render_structured_log(structured_log))

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


def _render_structured_log(log_data: dict[str, Any]) -> str:
    """Render structured log data as expandable section."""
    parts: list[str] = []
    parts.append("<details>")
    parts.append("<summary>Structured Log Data</summary>")

    # Block sequence
    blocks = log_data.get("block_sequence", [])
    if blocks:
        parts.append(f"<p><strong>Blocks:</strong> {', '.join(html.escape(str(b)) for b in blocks)}</p>")

    # Measurements
    measurements = log_data.get("measurements", [])
    if measurements:
        parts.append('<table class="measurements-table">')
        parts.append("<tr><th>Name</th><th>Value</th><th>Unit</th></tr>")
        for m in measurements:
            mname = html.escape(str(m.get("name", "")))
            mval = html.escape(str(m.get("value", "")))
            munit = html.escape(str(m.get("unit", "")))
            parts.append(f"<tr><td>{mname}</td><td>{mval}</td><td>{munit}</td></tr>")
        parts.append("</table>")

    # Results
    results = log_data.get("results", [])
    if results:
        parts.append("<p><strong>Results:</strong></p>")
        parts.append("<ul>")
        for r in results:
            parts.append(f"<li>{html.escape(str(r))}</li>")
        parts.append("</ul>")

    # Errors
    errors = log_data.get("errors", [])
    if errors:
        parts.append('<p><strong>Errors:</strong></p>')
        parts.append("<ul>")
        for e in errors:
            parts.append(f"<li>{html.escape(str(e))}</li>")
        parts.append("</ul>")

    # Rigging failure flag
    if log_data.get("has_rigging_failure"):
        parts.append('<p style="color:red"><strong>Rigging failure detected</strong></p>')

    parts.append("</details>")
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
    parts.append("<details>")
    parts.append("<summary>Inferred Dependencies</summary>")
    parts.append("<ul>")
    for dep in deps:
        dep_str = html.escape(str(dep.get("name", dep)))
        parts.append(f"<li>{dep_str}</li>")
    parts.append("</ul>")
    parts.append("</details>")
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
