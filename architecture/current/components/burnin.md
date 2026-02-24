# Component: Burn-in

## Location

`orchestrator/lifecycle/burnin.py`

## Purpose

Implements the burn-in lifecycle: a sweep loop that repeatedly runs burning_in tests until SPRT decides each one (stable or flaky), a stable demotion handler that re-runs failed stable tests to evaluate whether they should be demoted to flaky, a result processor that records orchestrator execution results and drives state transitions, a flaky deadline checker that auto-disables tests stuck in flaky state past a configurable deadline, and state filtering utilities.

## Interface

### BurnInSweep

```python
class BurnInSweep:
    def __init__(
        self, dag, status_file, commit_sha=None,
        max_iterations=200, timeout=300.0,
        target_hashes: dict[str, str] | None = None,
    )
    def run(self, test_names=None) -> SweepResult
```

When `target_hashes` is provided, SPRT evaluation uses only same-hash history entries (cross-session evidence pooling). Without target hashes, all history is used (existing behavior).

### SweepResult (dataclass)

```python
@dataclass
class SweepResult:
    decided: dict[str, str]    # test_name -> final state (stable/flaky)
    undecided: list[str]       # tests still burning_in after max_iterations
    total_runs: int            # total test executions performed
```

### handle_stable_failure

```python
def handle_stable_failure(test_name, dag, status_file, commit_sha=None, max_reruns=20, timeout=300.0) -> str
```

Returns `"demote"`, `"retain"`, or `"inconclusive"`. Uses the full persisted history from the status file for demotion evaluation, enabling cross-run demotion detection.

### process_results

```python
def process_results(results, status_file, commit_sha=None) -> list[tuple[str, str, str, str]]
```

Records orchestrator test results in the status file and evaluates lifecycle transitions. For each result (skipping `dependencies_failed`):
- **burning_in**: evaluates SPRT -> accept (stable) or reject (flaky)
- **stable + failed**: evaluates demotion on full persisted history -> demote (flaky), inconclusive (burning_in for closer monitoring), or retain (no change)
- **flaky / new**: records result only, no evaluation

Returns `(event_type, test_name, old_state, new_state)` tuples for each transition.

### sync_disabled_state

```python
def sync_disabled_state(dag, status_file) -> list[tuple[str, str, str, str]]
```

Synchronizes disabled flags from the DAG (manifest) with the status file. Tests marked `disabled=True` in the manifest are transitioned to "disabled" state. Tests in "disabled" state whose manifest no longer marks them disabled are transitioned to "new". Returns lifecycle event tuples.

### check_flaky_deadlines

```python
def check_flaky_deadlines(status_file, deadline_days: int) -> list[tuple[str, str, str, str]]
```

Scans all tests in `flaky` state and auto-disables any that have been flaky longer than `deadline_days`. Compares `last_updated` against UTC now. A negative `deadline_days` skips the check entirely (no deadline enforcement). Returns lifecycle event tuples and saves the status file if any transitions occur.

### filter_tests_by_state

```python
def filter_tests_by_state(dag, status_file, include_states=None) -> list[str]
```

Filters DAG tests by burn-in state. Tests not in the status file default to `stable`.

## State Transitions

```
new  -------->  burning_in  -------->  stable
(CI tool)          |    (SPRT accept)
                   |
                   +-------->  flaky
                   (SPRT reject)

stable  -------->  flaky
(demotion: SPRT demote on history)

stable  -------->  burning_in
(suspicious: SPRT inconclusive after failure)

flaky  -------->  burning_in
(CI tool deflake, history cleared, target_hash cleared)

flaky  -------->  disabled
(auto-disable after deadline via check_flaky_deadlines)

any  ---------->  disabled
(BUILD: disabled=True, sync at orchestrator start)

disabled  ----->  new
(BUILD: disabled=True removed, sync at orchestrator start)
```

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Test execution and node lookup
- **Executor** (`orchestrator.execution.executor.TestResult`): Result data structure
- **SPRT** (`orchestrator.lifecycle.sprt`): `sprt_evaluate` for burn-in decisions, `demotion_evaluate` for stable demotion
- **Status File** (`orchestrator.lifecycle.status.StatusFile`): State persistence, `get_same_hash_history` for evidence pooling

## Dependents

- **Orchestrator Main**: The `burn-in` subcommand triggers state transitions; invokes `process_results` after test execution when `--status-file` is configured; invokes `check_flaky_deadlines` at startup; invokes `BurnInSweep` during effort mode phase 3; uses `filter_tests_by_state` for burn-in test inclusion in regression

## Key Design Decisions

1. **Incremental save**: The status file is saved after every individual test run within the sweep loop, providing crash recovery. If the process is interrupted, already-decided tests retain their final state.

2. **SPRT as the decision engine**: Rather than using a fixed number of runs, SPRT provides statistically rigorous stopping criteria. The sweep loop continues until SPRT reaches a decision for each test or max_iterations is exhausted.

3. **Demotion via persisted history**: When a stable test fails, `handle_stable_failure` re-runs the test, records each result (with commit SHA) to the status file, and evaluates the full persisted history via `demotion_evaluate`. This enables cross-run demotion: failures that accumulate across separate CI invocations can trigger demotion, not just failures within a single session.

4. **Commit SHA propagation**: Both `BurnInSweep` and `handle_stable_failure` accept an optional `commit_sha` parameter that is recorded in each history entry via `record_run`. This enables correlating reliability changes with specific commits for root cause diagnostics.

5. **Default stable**: Tests not present in the status file are treated as stable by `filter_tests_by_state`, ensuring backward compatibility when burn-in is introduced to an existing project.

6. **Orchestrator integration via process_results**: Unlike `handle_stable_failure` (which re-runs tests), `process_results` operates on existing orchestrator results -- it records the outcome and evaluates SPRT without re-execution. This is the primary integration point between the orchestrator and the lifecycle state machine.

7. **Suspicious test escalation**: When a stable test fails but SPRT returns "inconclusive" (not enough evidence to demote), the test transitions to `burning_in` for closer monitoring. Counters and history are preserved (not reset) so the burn-in sweep can continue evaluating from accumulated data.

8. **Manifest-driven disabled sync**: The `sync_disabled_state` function bridges the BUILD file `disabled=True` flag with the persistent status file state. This runs at orchestrator startup before execution, ensuring disabled tests are excluded. When re-enabled, the test starts fresh as "new" and must go through burn-in again.

9. **Cross-session evidence pooling via target hashes**: When `target_hashes` is provided to `BurnInSweep`, each run is recorded with the target hash, and SPRT evaluation uses only same-hash history entries via `get_same_hash_history`. This enables evidence from prior sessions (with the same code state) to contribute to burn-in decisions, reaching stable/flaky classifications faster.

10. **Flaky deadline auto-disable**: `check_flaky_deadlines` enforces a time-based deadline on flaky tests. Tests that remain in `flaky` state beyond `deadline_days` are automatically transitioned to `disabled`. A negative deadline value disables the check entirely. This runs at orchestrator startup alongside `sync_disabled_state`.
