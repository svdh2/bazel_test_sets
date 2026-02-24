# Component: Status File

## Location

`orchestrator/lifecycle/status.py`

## Purpose

Manages the `.tests/status` JSON state file that tracks test maturity states and target content hashes. This is the persistent storage layer for the burn-in lifecycle and cross-session evidence pooling. Statistical parameters (`min_reliability`, `statistical_significance`) are passed directly to the constructor.

## Interface

### StatusFile

```python
class StatusFile:
    def __init__(
        self,
        path: str | Path,
        *,
        min_reliability: float | None = None,
        statistical_significance: float | None = None,
    )

    # Configuration (direct constructor parameters)
    min_reliability: float      # Property, default 0.99
    statistical_significance: float  # Property, default 0.95
    def set_config(min_reliability=None, statistical_significance=None)

    # Test state management
    def get_test_state(test_name: str) -> str | None
    def get_test_entry(test_name: str) -> dict | None
    def set_test_state(test_name, state, *, clear_history=False)
    def record_run(test_name: str, passed: bool, commit: str | None = None,
                   *, target_hash: str | None = None)
    def remove_test(test_name: str) -> bool

    # History
    def get_test_history(test_name: str) -> list[dict]
    def get_same_hash_history(test_name: str, target_hash: str) -> list[dict]

    # Target hash management
    def get_target_hash(test_name: str) -> str | None
    def set_target_hash(test_name: str, hash_value: str)
    def invalidate_evidence(test_name: str)

    # Queries
    def get_all_tests() -> dict[str, dict]
    def get_tests_by_state(state: str) -> list[str]

    # Persistence
    def save()

# Module-level helper
def runs_and_passes_from_history(history: list[dict]) -> tuple[int, int]
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
        {"passed": true, "commit": "abc123", "target_hash": "sha256:..."},
        {"passed": false, "commit": "def456", "target_hash": "sha256:..."}
      ],
      "last_updated": "2026-02-16T12:00:00+00:00",
      "target_hash": "sha256:..."
    }
  }
}
```

### Target Hash Fields

- **`target_hash`** (per-test entry): The current content hash of the test's Bazel target inputs (source files, data, dependencies). Set via `set_target_hash()` and used to detect when a test's inputs have changed.
- **`target_hash`** (per-history entry): The target hash at the time the run was recorded. Used by `get_same_hash_history()` to filter history entries for cross-session evidence pooling.

### Hash-Related Methods

- **`get_target_hash(test_name)`**: Returns the stored target hash for a test, or None.
- **`set_target_hash(test_name, hash_value)`**: Sets the target hash. Creates the test entry if it doesn't exist (state `"new"`).
- **`invalidate_evidence(test_name)`**: Clears history, transitions state to `burning_in`, and updates `last_updated`. The `target_hash` field is preserved (caller sets the new hash separately). Used when a test's content hash changes, invalidating all accumulated SPRT evidence.
- **`get_same_hash_history(test_name, target_hash)`**: Filters the test's history to entries whose `target_hash` field matches the given hash. Returns newest-first order. Used for cross-session evidence pooling in SPRT evaluation.
- **`record_run(target_hash=...)`**: When `target_hash` is provided, the hash is stored in the history entry alongside `passed` and `commit`.

## Dependencies

- **Config** (`orchestrator/lifecycle/config.py`): `DEFAULT_CONFIG` provides fallback values for `min_reliability` and `statistical_significance`

## Dependents

- **Burn-in** (`orchestrator.lifecycle.burnin`): Reads/writes test states and history during sweep and result processing; uses `get_same_hash_history` when target hashes are available
- **Effort Runner** (`orchestrator.execution.effort`): Records reruns, reads `min_reliability` and `statistical_significance` for SPRT, uses `get_same_hash_history` for evidence pooling
- **Orchestrator Main** (`orchestrator.main`): Creates StatusFile with `min_reliability` and `statistical_significance` from CLI args; calls hash management methods via `_compute_and_filter_hashes`; lifecycle subcommands (burn-in, deflake, test-status) also use StatusFile

## Key Design Decisions

1. **Corruption recovery**: If the status file contains invalid JSON, the class starts fresh with default configuration rather than crashing. This prevents a corrupted file from blocking all CI operations.

2. **Auto-create on record_run**: If `record_run` is called for a test not in the file, it creates an entry with state `"new"`. This supports incremental adoption -- tests can be tracked before explicitly entering the burn-in workflow.

3. **State validation**: `set_test_state` validates that the state is one of the five valid states, raising `ValueError` for invalid transitions. The state machine semantics (which transitions are allowed) are enforced by the burn-in and CI tool logic, not by StatusFile itself.

4. **Parent directory creation**: The `save()` method creates parent directories if needed, supporting first-time initialization without manual directory setup.

5. **Capped history with commit SHAs and target hashes**: Each `record_run` prepends a `{passed, commit, target_hash}` entry to the test's history. The history is capped at 200 entries (oldest dropped) and stored newest-first. Commit SHAs enable correlating reliability changes with specific commits. Target hashes enable cross-session evidence pooling. When `set_test_state` is called with `clear_history=True` (deflake/burn-in scenarios), the history is cleared.

6. **History-derived aggregates**: Run counts and pass counts are derived from the history array via `runs_and_passes_from_history()` rather than stored separately. This eliminates redundancy and keeps the history as the single source of truth for reliability calculations.

7. **Backward compatibility**: Old status files without a `history` field load without error; `get_test_history` returns an empty list for entries that lack the field. Old files with legacy `runs`/`passes` keys are loaded without error; those keys are simply ignored and dropped on the next `save()`. History entries without `target_hash` are excluded by `get_same_hash_history` but included in `get_test_history`.

8. **Direct constructor parameters**: Statistical parameters (`min_reliability`, `statistical_significance`) are passed directly to the `StatusFile` constructor rather than read from a separate config file. This allows the `ci_gate` rule to bake these values into the runner script as CLI flags.

9. **Evidence invalidation preserves entry**: `invalidate_evidence` clears history and transitions to `burning_in` but preserves the test entry (including any separately-set `target_hash`). This ensures the test re-enters the burn-in workflow with a clean slate while maintaining its hash tracking.
