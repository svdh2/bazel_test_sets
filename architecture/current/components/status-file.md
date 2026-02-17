# Component: Status File

## Location

`orchestrator/lifecycle/status.py`

## Purpose

Manages the `.tests/status` JSON state file that tracks test maturity states. This is the persistent storage layer for the burn-in lifecycle. Configuration is delegated to a separate [TestSetConfig](test-set-config.md) instance.

## Interface

### StatusFile

```python
class StatusFile:
    def __init__(self, path: str | Path, config_path: Path | None = None)

    # Configuration (delegated to TestSetConfig)
    min_reliability: float      # Property, default 0.99
    statistical_significance: float  # Property, default 0.95
    def set_config(min_reliability=None, statistical_significance=None)

    # Test state management
    def get_test_state(test_name: str) -> str | None
    def get_test_entry(test_name: str) -> dict | None
    def set_test_state(test_name, state, *, clear_history=False)
    def record_run(test_name: str, passed: bool, commit: str | None = None)
    def remove_test(test_name: str) -> bool

    # History
    def get_test_history(test_name: str) -> list[dict]

# Module-level helper
def runs_and_passes_from_history(history: list[dict]) -> tuple[int, int]

    # Queries
    def get_all_tests() -> dict[str, dict]
    def get_tests_by_state(state: str) -> list[str]

    # Persistence
    def save()
```

### Valid States

`new`, `burning_in`, `stable`, `flaky`, `disabled`

### File Format

```json
{
  "tests": {
    "//test:a": {
      "state": "stable",
      "history": [
        {"passed": true, "commit": "abc123"},
        {"passed": false, "commit": "def456"}
      ],
      "last_updated": "2026-02-16T12:00:00+00:00"
    }
  }
}
```

Configuration (`min_reliability`, `statistical_significance`) is stored separately in a `.test_set_config` file at the Bazel workspace root. See [TestSetConfig](test-set-config.md).

The `history` array is ordered newest-first and capped at 200 entries (`HISTORY_CAP`). Each entry records a pass/fail result and the git commit SHA (or `null` if unavailable). Oldest entries are dropped when the cap is exceeded.

## Dependencies

- [TestSetConfig](test-set-config.md) (`orchestrator/lifecycle/config.py`): Configuration is delegated to this component

## Dependents

- **Burn-in** (`orchestrator.lifecycle.burnin`): Reads/writes test states and history during sweep and result processing
- **Orchestrator Main** (`orchestrator.main`): Loads StatusFile when `status_file` is configured in `.test_set_config`, passes it to `process_results` after test execution
- **CI Tool** (`ci_tool/main.py`): All subcommands (burn-in, deflake, test-status) use StatusFile
- **Regression Selector**: Could filter by burn-in state (currently treats all manifest tests as candidates)

## Key Design Decisions

1. **Corruption recovery**: If the status file contains invalid JSON, the class starts fresh with default configuration rather than crashing. This prevents a corrupted file from blocking all CI operations.

2. **Auto-create on record_run**: If `record_run` is called for a test not in the file, it creates an entry with state `"new"`. This supports incremental adoption -- tests can be tracked before explicitly entering the burn-in workflow.

3. **State validation**: `set_test_state` validates that the state is one of the five valid states, raising `ValueError` for invalid transitions. The state machine semantics (which transitions are allowed) are enforced by the burn-in and CI tool logic, not by StatusFile itself.

4. **Parent directory creation**: The `save()` method creates parent directories if needed, supporting first-time initialization without manual directory setup.

5. **Capped history with commit SHAs**: Each `record_run` prepends a `{passed, commit}` entry to the test's history. The history is capped at 200 entries (oldest dropped) and stored newest-first. Commit SHAs enable correlating reliability changes with specific commits for root cause diagnostics. When `set_test_state` is called with `clear_history=True` (deflake/burn-in scenarios), the history is cleared.

6. **History-derived aggregates**: Run counts and pass counts are derived from the history array via `runs_and_passes_from_history()` rather than stored separately. This eliminates redundancy and keeps the history as the single source of truth for reliability calculations.

7. **Backward compatibility**: Old status files without a `history` field load without error; `get_test_history` returns an empty list for entries that lack the field. Old files with legacy `runs`/`passes` keys are loaded without error; those keys are simply ignored and dropped on the next `save()`.
