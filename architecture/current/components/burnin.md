# Component: Burn-in

## Location

`orchestrator/lifecycle/burnin.py`

## Purpose

Implements the burn-in lifecycle: a sweep loop that repeatedly runs burning_in tests until SPRT decides each one (stable or flaky), and a stable demotion handler that re-runs failed stable tests to evaluate whether they should be demoted to flaky.

## Interface

### BurnInSweep

```python
class BurnInSweep:
    def __init__(self, dag, status_file, max_iterations=200, timeout=300.0)
    def run(self, test_names=None) -> SweepResult
```

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
def handle_stable_failure(test_name, dag, status_file, max_reruns=20, timeout=300.0) -> str
```

Returns `"demote"`, `"retain"`, or `"inconclusive"`.

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
(demotion after repeated failure)

flaky  -------->  burning_in
(CI tool deflake, counters reset)
```

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Test execution and node lookup
- **Executor** (`orchestrator.execution.executor.TestResult`): Result data structure
- **SPRT** (`orchestrator.lifecycle.sprt`): `sprt_evaluate` for burn-in decisions, `demotion_evaluate` for stable demotion
- **Status File** (`orchestrator.lifecycle.status.StatusFile`): State persistence

## Dependents

- **CI Tool**: The `burn-in` subcommand triggers state transitions; the sweep loop would be invoked via orchestrator integration
- **Orchestrator Main**: Could invoke burn-in sweep as part of a run

## Key Design Decisions

1. **Incremental save**: The status file is saved after every individual test run within the sweep loop, providing crash recovery. If the process is interrupted, already-decided tests retain their final state.

2. **SPRT as the decision engine**: Rather than using a fixed number of runs, SPRT provides statistically rigorous stopping criteria. The sweep loop continues until SPRT reaches a decision for each test or max_iterations is exhausted.

3. **Demotion via reverse-chronological SPRT**: When a stable test fails, recent history is evaluated newest-first. This gives more weight to recent behavior when deciding whether the test has become flaky.

4. **Default stable**: Tests not present in the status file are treated as stable by `filter_tests_by_state`, ensuring backward compatibility when burn-in is introduced to an existing project.
