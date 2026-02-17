# Component: Reporter

## Location

`orchestrator/reporting/reporter.py`

## Purpose

Collects test execution results and generates JSON reports. Supports both flat and hierarchical (DAG-mirroring) report structures, structured log data integration, burn-in progress, regression selection details, and rolling history for reverse-chronological SPRT.

## Interface

### Reporter

```python
class Reporter:
    def __init__(self)

    # Data collection
    def add_result(self, result: TestResult)
    def add_results(self, results: list[TestResult])
    def set_manifest(self, manifest: dict)
    def set_commit_hash(self, commit_hash: str)
    def add_structured_log(self, test_name, parsed_output)
    def add_burn_in_progress(self, test_name, progress)
    def set_regression_selection(self, selection_data)
    def add_inferred_dependencies(self, test_name, deps)
    def set_e_value_verdict(self, verdict_data)

    # Report generation
    def generate_report(self) -> dict
    def generate_report_with_history(self, existing_report_path=None) -> dict

    # File output
    def write_report(self, path: Path)
    def write_report_with_history(self, path, existing_path=None)
```

### Report Structure (JSON)

```yaml
report:
  generated_at: "ISO timestamp"
  commit: "abc123"
  summary:
    total: 10
    passed: 8
    failed: 1
    dependencies_failed: 1
    total_duration_seconds: 12.345
  test_set:                    # Hierarchical (when manifest is set)
    name: "auth_tests"
    assertion: "..."
    status: "failed"           # Aggregated from children
    tests:
      "//test:a":
        assertion: "..."
        status: "passed"
        duration_seconds: 1.234
        structured_log: {...}
        burn_in: {...}
        inferred_dependencies: [...]
  regression_selection: {...}  # When --regression flag was used
  e_value_verdict:              # When --verdict flag was used
    verdict: "GREEN"            # GREEN, RED, or UNDECIDED
    e_set: 0.001
    min_s_value: 1000.0
    red_threshold: 20.0
    green_threshold: 40.0
    n_tests: 2
    weakest_test: "//test:b"
    per_test: [...]
  history:                     # When using generate_report_with_history
    "//test:a":
      - status: "passed"
        duration_seconds: 1.234
        timestamp: "..."
        commit: "abc123"
```

### Status Aggregation

| Child statuses | Aggregated |
|---------------|------------|
| All `passed` | `passed` |
| Any `failed` or `failed+dependencies_failed` | `failed` |
| No results | `no_tests` |
| Otherwise | `mixed` |

## Dependencies

- **Executor** (`orchestrator.execution.executor.TestResult`): Input data structure
- Standard library: `json` (serialization)

## Dependents

- **Orchestrator Main**: Creates Reporter, adds results, writes JSON
- **HTML Reporter**: Consumes the report dict from `generate_report()`

## Key Design Decisions

1. **Hierarchical vs flat**: When a manifest is set, the report mirrors the DAG structure with the test_set at the top and test entries nested underneath. Without a manifest, a flat list of test results is produced.

2. **Rolling history with trimming**: `generate_report_with_history` loads an existing report, appends current results, and trims to `MAX_HISTORY` (500) entries per test. This provides a bounded reverse-chronological record for SPRT demotion evaluation.

3. **Five-status model**: The reporter supports all five statuses including the combined race-condition statuses (`passed+dependencies_failed`, `failed+dependencies_failed`), ensuring no information is lost during reporting.

4. **Optional enrichment**: Structured logs, burn-in progress, inferred dependencies, regression selection, and E-value verdict data are all optional additions. The reporter works with just TestResult objects for simple use cases.
