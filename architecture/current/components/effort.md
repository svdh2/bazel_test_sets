# Component: Effort Runner

## Location

`orchestrator/execution/effort.py`

## Purpose

SPRT-based rerun engine for converge and max effort modes. Reruns tests using the Sequential Probability Ratio Test to classify each test as true_pass, true_fail, flake, or undecided within a single session. Only session-local run data is used for SPRT evaluation (no historic data from previous code states). All reruns are recorded to the status file for persistence.

## Interface

### Data Types

```python
@dataclass
class EffortClassification:
    test_name: str
    classification: str      # "true_pass", "true_fail", "flake", "undecided"
    initial_status: str       # "passed" or "failed"
    runs: int                 # total session runs (including initial)
    passes: int               # total session passes
    sprt_decision: str        # "accept", "reject", "continue", "not_evaluated"

@dataclass
class EffortResult:
    classifications: dict[str, EffortClassification]
    total_reruns: int
```

### Public API

```python
class EffortRunner:
    def __init__(
        self,
        dag: TestDAG,
        status_file: StatusFile,
        commit_sha: str,
        max_reruns: int = 100,
        effort_mode: str = "converge",
        initial_results: list[TestResult] | None = None,
        timeout: float = 300.0,
    ) -> None: ...

    def run(self) -> EffortResult: ...
```

### Helper

```python
def _classify(
    name: str,
    initial_status: str,
    sprt_decision: str,
    runs: int,
    passes: int,
) -> EffortClassification: ...
```

## SPRT Classification Logic

| Initial status | SPRT accept (H0: reliable) | SPRT reject (H1: unreliable) | Budget exhausted |
|---|---|---|---|
| failed | **flake** | **true_fail** | undecided |
| passed | **true_pass** | **flake** | undecided |

Non-targeted tests in converge mode (initially passing) get classification `true_pass` with `sprt_decision="not_evaluated"`.

## Effort Modes

- **converge**: Only reruns initially-failed tests. Initially-passing tests are classified as `true_pass` without SPRT evaluation. Used at merge-stage CI.
- **max**: Reruns all tests (both passing and failing). Used for release validation.

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Provides test node metadata (executable paths)
- **Executor** (`orchestrator.execution.executor.TestResult`): Test result data type
- **SPRT** (`orchestrator.lifecycle.sprt.sprt_evaluate`): Core SPRT decision function
- **Status File** (`orchestrator.lifecycle.status.StatusFile`): Records reruns and provides config (min_reliability, statistical_significance)

## Dependents

- **Orchestrator Main** (`orchestrator.main._run_effort`): Creates EffortRunner and calls `run()` for converge/max effort modes

## Key Design Decisions

1. **Session-only SPRT data**: SPRT evaluation uses only runs/passes from the current session, not historic data from previous commits. This ensures classifications reflect current code behavior. All reruns are still recorded to the status file for persistence and downstream use.

2. **Round-robin iteration**: Each target test gets one rerun per round, ensuring fair budget distribution across tests. Tests are removed from the target set as soon as SPRT reaches a decision.

3. **Per-test budget**: Each test has its own budget of `max_reruns` reruns (not counting the initial run). When the budget is exhausted without SPRT convergence, the test is classified as `undecided`.

4. **Subprocess execution**: Uses the same subprocess pattern as HiFiEvaluator, running test executables directly with configurable timeout.
