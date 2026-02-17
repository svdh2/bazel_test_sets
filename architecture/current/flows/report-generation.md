# Flow: Report Generation

## Purpose

After test execution, generate structured reports in JSON and HTML formats. Reports include test results, timing, burn-in progress, regression selection details, and optionally rolling history for SPRT demotion analysis. Structured log data embedded in stdout (`[TST]` lines) is parsed at HTML render time.

## Trigger

Test execution with `--output` flag:

```bash
bazel run //path/to:my_test_set -- --output target/reports/my_tests.json
```

## Steps

### 1. Result Collection

After execution completes, the orchestrator passes all `TestResult` objects to the Reporter.

```python
reporter = Reporter()
reporter.add_results(results)
```

**Components**: Orchestrator Main, Reporter

### 2. JSON Report Generation

```python
reporter.write_report(args.output)
```

The Reporter:
1. Computes summary statistics (total, passed, failed, dependencies_failed, duration)
2. If a manifest is set, builds a hierarchical report mirroring the DAG structure; otherwise, builds a flat report
3. Formats each test result with status, duration, exit code, stdout/stderr
4. Adds optional enrichment data (burn-in progress, inferred dependencies)
5. Serializes to JSON and writes to disk

**Components**: Reporter

### 3. HTML Report Generation

```python
report_data = reporter.generate_report()
write_html_report(report_data, html_path)
```

The HTML Reporter:
1. Takes the same report data structure used for JSON
2. Renders a self-contained HTML page with embedded CSS
3. Parses stdout with `parse_stdout_segments()` to detect `[TST]` structured logging and renders unified view (block cards with measurements tables, assertions, features) or falls back to raw `<pre>` for plain stdout
4. Writes to the same path as JSON but with `.html` extension

**Components**: HTML Reporter

## Data Flow

```
list[TestResult]
    |
    v
Reporter.add_results()
    |
    v
Reporter.generate_report()
    |
    +---> _compute_summary()
    |         |
    |         v
    |     {total, passed, failed, ..., total_duration_seconds}
    |
    +---> _build_hierarchical_report()  (if manifest set)
    |     or _format_result() per result (flat mode)
    |         |
    |         v
    |     {test_set: {name, assertion, status, tests: {...}}}
    |
    v
report_data = {"report": {...}}
    |
    +---> json.dump() -> my_tests.json
    |
    +---> generate_html_report() -> my_tests.html
```

## Report Content

### JSON Report Structure

```json
{
  "report": {
    "generated_at": "2026-02-16T12:00:00+00:00",
    "commit": "2f9daa5...",
    "summary": {
      "total": 11,
      "passed": 10,
      "failed": 1,
      "dependencies_failed": 0,
      "passed+dependencies_failed": 0,
      "failed+dependencies_failed": 0,
      "total_duration_seconds": 8.234
    },
    "test_set": {
      "name": "ecommerce_tests",
      "assertion": "E-commerce platform passes all tests",
      "status": "failed",
      "tests": {
        "//ecommerce:credit_card_wrapped": {
          "assertion": "Credit card payments authorized",
          "status": "passed",
          "duration_seconds": 0.420,
          "exit_code": 0,
          "stdout": "[TST] {\"type\": \"block_start\", \"block\": \"rigging\"}..."
        }
      }
    }
  }
}
```

### HTML Report Elements

| Section | Content |
|---------|---------|
| Header | Title, timestamp, commit hash |
| Summary bar | Color-coded count badges + duration |
| Test set block | Hierarchical with aggregated status badge |
| Per-test entry | Status badge, assertion, duration, expandable logs |
| Block segments | Structured blocks parsed from stdout: type badge, features, measurements table, assertions, errors |
| Burn-in info | Runs, passes, SPRT status (blue info box) |
| Regression info | Changed files, test scores table |

## Rolling History (Advanced)

When using `generate_report_with_history`:

1. Load existing JSON report
2. Extract per-test history entries
3. Append current results
4. Trim to 500 entries per test (MAX_HISTORY)
5. Include in the new report under `history` key

This enables reverse-chronological SPRT for demotion analysis across multiple CI runs.

## Output Files

Given `--output target/reports/my_tests.json`:

| File | Format | Content |
|------|--------|---------|
| `target/reports/my_tests.json` | JSON | Full structured report |
| `target/reports/my_tests.html` | HTML | Self-contained visual report |
