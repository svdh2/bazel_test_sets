# Component: Status File

## Location

- `orchestrator/lifecycle/status.py` — `StatusFile` facade
- `orchestrator/lifecycle/backend/base.py` — `StorageBackend` ABC
- `orchestrator/lifecycle/backend/sqlite.py` — `SqliteBackend` implementation

## Purpose

Manages test maturity states and target content hashes via a modular storage backend. This is the persistent storage layer for the burn-in lifecycle and cross-session evidence pooling. The default `SqliteBackend` stores data in an in-memory SQLite database and persists it to CSV files (`tests.csv`, `history.csv`) inside a directory, keeping only git-friendly text on disk. Statistical parameters (`min_reliability`, `statistical_significance`) are passed directly to the constructor.

## Backend Architecture

```
StatusFile (public API — facade)
    |
    v
StorageBackend (ABC)        — orchestrator/lifecycle/backend/base.py
    |
    +-- SqliteBackend        — orchestrator/lifecycle/backend/sqlite.py
    +-- (future: API client)  — e.g. REST/gRPC backend service
```

`StorageBackend` is an abstract base class in `backend/base.py` defining methods for test CRUD, history CRUD, load/persist, and legacy JSON loading. `SqliteBackend` in `backend/sqlite.py` implements all operations via an in-memory SQLite database. The backend is injected into `StatusFile` via an optional `engine` keyword argument.

At scale, the `StorageBackend` interface allows replacing the direct storage implementation with a backend API client (e.g. REST/gRPC) that delegates to an external results database service, removing the need for the orchestrator to manage persistence directly.

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
        engine: StorageBackend | None = None,
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
    def clear_target_hash(test_name: str)
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

### Storage Format

The `path` argument is a **directory** containing two CSV files:

**`tests.csv`**:
```csv
test_name,state,target_hash,last_updated
//test:a,stable,sha256:abc123,2026-02-16T12:00:00+00:00
//test:b,burning_in,,2026-02-15T08:00:00+00:00
```

**`history.csv`**:
```csv
id,test_name,passed,commit_sha,target_hash
1,//test:a,1,abc123,sha256:abc123
2,//test:a,0,def456,sha256:abc123
3,//test:b,1,ghi789,
```

### SQL Schema (in-memory SQLite)

```sql
CREATE TABLE tests (
    test_name    TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    target_hash  TEXT,
    last_updated TEXT NOT NULL
);

CREATE TABLE history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    test_name   TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    commit_sha  TEXT,
    target_hash TEXT,
    FOREIGN KEY (test_name) REFERENCES tests(test_name) ON DELETE CASCADE
);
```

History ordering uses the `id` column: newest entries have the highest `id`, queries use `ORDER BY id DESC`.

### Target Hash Fields

- **`target_hash`** (per-test entry / `tests` table): The current content hash of the test's Bazel target inputs (source files, data, dependencies). Set via `set_target_hash()` and used to detect when a test's inputs have changed.
- **`target_hash`** (per-history entry / `history` table): The target hash at the time the run was recorded. Used by `get_same_hash_history()` to filter history entries for cross-session evidence pooling.

### Hash-Related Methods

- **`get_target_hash(test_name)`**: Returns the stored target hash for a test, or None.
- **`set_target_hash(test_name, hash_value)`**: Sets the target hash. Creates the test entry if it doesn't exist (state `"new"`).
- **`clear_target_hash(test_name)`**: Clears the target hash for a test (sets to None). Used by `cmd_deflake`.
- **`invalidate_evidence(test_name)`**: Clears history, transitions state to `burning_in`, and updates `last_updated`. The `target_hash` field is preserved (caller sets the new hash separately). Used when a test's content hash changes, invalidating all accumulated SPRT evidence.
- **`get_same_hash_history(test_name, target_hash)`**: Filters the test's history to entries whose `target_hash` field matches the given hash. Returns newest-first order. Used for cross-session evidence pooling in SPRT evaluation.
- **`record_run(target_hash=...)`**: When `target_hash` is provided, the hash is stored in the history entry alongside `passed` and `commit`.

## Dependencies

- **Config** (`orchestrator/lifecycle/config.py`): `DEFAULT_CONFIG` provides fallback values for `min_reliability` and `statistical_significance`
- **sqlite3** (Python stdlib): In-memory SQL database
- **csv** (Python stdlib): CSV file reading/writing

## Dependents

- **Burn-in** (`orchestrator.lifecycle.burnin`): Reads/writes test states and history during sweep and result processing; uses `get_same_hash_history` when target hashes are available
- **Effort Runner** (`orchestrator.execution.effort`): Records reruns, reads `min_reliability` and `statistical_significance` for SPRT, uses `get_same_hash_history` for evidence pooling
- **Orchestrator Main** (`orchestrator.main`): Creates StatusFile with `min_reliability` and `statistical_significance` from CLI args; calls hash management methods via `_compute_and_filter_hashes`; lifecycle subcommands (burn-in, deflake, test-status) also use StatusFile

## Key Design Decisions

1. **Modular storage backend**: The `StorageBackend` ABC (`backend/base.py`) allows swapping the storage implementation. `SqliteBackend` (`backend/sqlite.py`) is the default, persisting data as CSV files in git. At scale, a backend API client implementing the same interface can delegate to an external results database service (e.g. REST/gRPC), removing direct storage management from the orchestrator. The backend is injected via the `StatusFile` constructor.

2. **CSV persistence for git**: The SQLite database is in-memory only — no binary `.db` files are stored. On `save()`, data is dumped to `tests.csv` and `history.csv` (sorted, deterministic output). On construction, CSV files are loaded into the in-memory database. This keeps all persistent data as human-readable text in git.

3. **Corruption recovery**: If CSV files contain invalid data, the backend catches parsing errors and starts fresh rather than crashing. This prevents corrupted files from blocking CI operations.

4. **Auto-create on record_run**: If `record_run` is called for a test not in the database, it creates an entry with state `"new"`. This supports incremental adoption — tests can be tracked before explicitly entering the burn-in workflow.

5. **State validation**: `set_test_state` validates that the state is one of the five valid states, raising `ValueError` for invalid transitions. The state machine semantics (which transitions are allowed) are enforced by the burn-in and CI tool logic, not by StatusFile itself.

6. **Directory creation**: The `save()` method creates the status directory and parent directories if needed, supporting first-time initialization without manual setup.

7. **Capped history**: Each `record_run` inserts a history row. After each insert, rows exceeding the 200-entry cap (per test) are deleted. History is ordered newest-first via the `AUTOINCREMENT` id column.

8. **History-derived aggregates**: Run counts and pass counts are derived from the history via `runs_and_passes_from_history()` rather than stored separately. This eliminates redundancy and keeps history as the single source of truth.

9. **JSON backward compatibility**: If `path` points to an existing file (not a directory), it is treated as a legacy JSON status file and loaded via `load_from_json_data()`. On the next `save()`, the file is replaced with a CSV directory. This enables seamless migration from the old format.

10. **Direct constructor parameters**: Statistical parameters (`min_reliability`, `statistical_significance`) are passed directly to the `StatusFile` constructor rather than stored in the database. This allows the `ci_gate` rule to bake these values into the runner script as CLI flags.

11. **Evidence invalidation preserves entry**: `invalidate_evidence` clears history and transitions to `burning_in` but preserves the test entry (including any separately-set `target_hash`). This ensures the test re-enters the burn-in workflow with a clean slate while maintaining its hash tracking.

12. **CASCADE delete**: Removing a test via `remove_test()` automatically deletes all its history entries through the SQL foreign key `ON DELETE CASCADE` constraint.
