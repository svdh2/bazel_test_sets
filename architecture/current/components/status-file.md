# Component: Status File

## Location

`orchestrator/lifecycle/status.py`

## Purpose

Manages the `.tests/status` JSON state file that tracks test maturity states, run counts, and configuration. This is the persistent storage layer for the burn-in lifecycle.

## Interface

### StatusFile

```python
class StatusFile:
    def __init__(self, path: str | Path)

    # Configuration
    min_reliability: float      # Property, default 0.99
    statistical_significance: float  # Property, default 0.95
    def set_config(min_reliability=None, statistical_significance=None)

    # Test state management
    def get_test_state(test_name: str) -> str | None
    def get_test_entry(test_name: str) -> dict | None
    def set_test_state(test_name, state, runs=None, passes=None)
    def record_run(test_name: str, passed: bool, commit: str | None = None)
    def remove_test(test_name: str) -> bool

    # History
    def get_test_history(test_name: str) -> list[dict]

    # Queries
    def get_all_tests() -> dict[str, dict]
    def get_tests_by_state(state: str) -> list[str]

    # Persistence
    def save()
```

### Valid States

`new`, `burning_in`, `stable`, `flaky`

### File Format

```json
{
  "config": {
    "min_reliability": 0.99,
    "statistical_significance": 0.95
  },
  "tests": {
    "//test:a": {
      "state": "stable",
      "runs": 50,
      "passes": 50,
      "history": [
        {"passed": true, "commit": "abc123"},
        {"passed": false, "commit": "def456"}
      ],
      "last_updated": "2026-02-16T12:00:00+00:00"
    }
  }
}
```

The `history` array is ordered newest-first and capped at 200 entries (`HISTORY_CAP`). Each entry records a pass/fail result and the git commit SHA (or `null` if unavailable). Oldest entries are dropped when the cap is exceeded.

## Dependencies

- None (pure Python with standard library JSON and pathlib)

## Dependents

- **Burn-in** (`orchestrator.lifecycle.burnin`): Reads/writes test states and run counts during sweep and result processing
- **Orchestrator Main** (`orchestrator.main`): Loads StatusFile when `--status-file` is provided, passes it to `process_results` after test execution
- **CI Tool** (`ci_tool/main.py`): All subcommands (burn-in, deflake, test-status) use StatusFile
- **Regression Selector**: Could filter by burn-in state (currently treats all manifest tests as candidates)

## Key Design Decisions

1. **Corruption recovery**: If the status file contains invalid JSON, the class starts fresh with default configuration rather than crashing. This prevents a corrupted file from blocking all CI operations.

2. **Auto-create on record_run**: If `record_run` is called for a test not in the file, it creates an entry with state `"new"`. This supports incremental adoption -- tests can be tracked before explicitly entering the burn-in workflow.

3. **State validation**: `set_test_state` validates that the state is one of the four valid states, raising `ValueError` for invalid transitions. The state machine semantics (which transitions are allowed) are enforced by the burn-in and CI tool logic, not by StatusFile itself.

4. **Parent directory creation**: The `save()` method creates parent directories if needed, supporting first-time initialization without manual directory setup.

5. **Capped history with commit SHAs**: Each `record_run` prepends a `{passed, commit}` entry to the test's history. The history is capped at 200 entries (oldest dropped) and stored newest-first. Commit SHAs enable correlating reliability changes with specific commits for root cause diagnostics. When `set_test_state` resets counters to zero (deflake/burn-in scenarios), the history is also cleared.

6. **Backward compatibility**: Old status files without a `history` field load without error; `get_test_history` returns an empty list for entries that lack the field.
