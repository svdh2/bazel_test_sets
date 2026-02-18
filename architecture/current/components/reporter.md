# Component: Reporter

## Location

`orchestrator/reporting/reporter.py`

## Purpose

Collects test execution results and generates JSON reports. Supports both flat and hierarchical (DAG-mirroring) report structures, burn-in progress, regression selection details, and rolling history for reverse-chronological SPRT.

## Interface

### Reporter

```python
class Reporter:
    def __init__(self)

    # Output (populated after report generation)
    reliability_demoted_tests: list[str]  # Tests demoted to flaky by reliability check

    # Data collection
    def add_result(self, result: TestResult)
    def add_results(self, results: list[TestResult])
    def set_manifest(self, manifest: dict)
    def set_commit_hash(self, commit_hash: str)
    def add_burn_in_progress(self, test_name, progress)
    def set_regression_selection(self, selection_data)
    def add_inferred_dependencies(self, test_name, deps)
    def set_e_value_verdict(self, verdict_data)
    def set_lifecycle_data(self, data: dict)
    def set_lifecycle_config(self, config: dict)
    def set_source_link_base(self, base: str | None)

    # Report generation
    def generate_report(self) -> dict
    def generate_report_with_history(self, existing_report_path=None) -> dict

    # File output
    def write_report(self, path: Path)
    def write_report_with_history(self, path, existing_path=None)
```

### Report Structure (JSON)

```json
{
  "report": {
    "generated_at": "ISO timestamp",
    "commit": "abc123",
    "summary": {
      "total": 10,
      "passed": 8,
      "failed": 1,
      "dependencies_failed": 1,
      "total_duration_seconds": 12.345
    },
    "test_set": {                    // Hierarchical (when manifest is set)
      "name": "auth_tests",
      "assertion": "...",
      "status": "failed",           // Aggregated from children
      "tests": {
        "//test:a": {
          "assertion": "...",
          "status": "passed",
          "duration_seconds": 1.234,
          "burn_in": {},
          "inferred_dependencies": [],
          "lifecycle": {               // When status_file is configured
            "state": "stable",        // new, burning_in, stable, flaky, disabled
            "runs": 125,
            "passes": 124,
            "reliability": 0.992
          }
        }
      },
      "lifecycle_summary": {           // Aggregated from children (when lifecycle data present)
        "total": 7,
        "stable": 5,
        "burning_in": 1,
        "flaky": 1,
        "aggregate_runs": 750,
        "aggregate_passes": 745,
        "aggregate_reliability": 0.9933
      }
    },
    "lifecycle_config": {              // When status_file is configured
      "min_reliability": 0.99,
      "statistical_significance": 0.95
    },
    "source_link_base": "https://github.com/owner/repo/blob/<sha>",  // When git remote is GitHub and commit is clean
    "regression_selection": {},  // When --regression flag was used
    "e_value_verdict": {              // When --verdict flag was used
      "verdict": "GREEN",            // GREEN, RED, or UNDECIDED
      "e_set": 0.001,
      "min_s_value": 1000.0,
      "red_threshold": 20.0,
      "green_threshold": 40.0,
      "n_tests": 2,
      "weakest_test": "//test:b",
      "per_test": []
    }
  }
}
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

4. **Optional enrichment**: Burn-in progress, inferred dependencies, regression selection, E-value verdict data, lifecycle state, and source link base are all optional additions. The reporter works with just TestResult objects for simple use cases. Structured log data in stdout is parsed at HTML render time by the log parser's `parse_stdout_segments()` function, not stored as a separate report field.

5. **Lifecycle aggregation**: When `set_lifecycle_data()` is called, each test set node includes a `lifecycle_summary` with state counts and aggregate reliability computed bottom-up through the tree. The `lifecycle_config` thresholds are included at the report top level so readers understand what "stable" and "flaky" mean quantitatively.

6. **History-based reliability**: When `generate_report_with_history()` is used, lifecycle `runs`/`passes`/`reliability` are recomputed from the accumulated rolling history rather than using StatusFile counters. StatusFile counters reset on lifecycle transitions (e.g. flaky → burning_in resets to 0/0), but the rolling history accumulates across all runs. Recomputing from history ensures the displayed reliability percentage matches the visible timeline. The lifecycle `state` still comes from the StatusFile (but may be overridden by reliability demotion — see below). Statuses `dependencies_failed` are excluded from reliability counts (test wasn't executed).

7. **Reliability-based flaky demotion**: After updating lifecycle data from rolling history, `_update_node_lifecycle()` checks each test's computed reliability against `min_reliability` from `lifecycle_config`. If a test has `runs > 0`, is not `disabled`, and its reliability falls below the threshold, its lifecycle state is overridden to `"flaky"` regardless of the StatusFile state. Demoted test names are tracked in `Reporter.reliability_demoted_tests` so callers (e.g. `main.py`) can report them and set a non-zero exit code.

8. **Status re-aggregation after demotion**: After lifecycle updates, `_update_node_lifecycle()` re-aggregates each test set node's status. Tests whose rolling history reliability is below `min_reliability` are counted as `"failed"` for aggregation purposes, overriding their execution status. This ensures that a test set containing an unreliable test shows `"failed"` even if all individual test executions passed. The check uses `reliability < min_reliability` (not `state == "flaky"`), so a test marked flaky in the StatusFile but with improved reliability above the threshold will not drag down the set.
