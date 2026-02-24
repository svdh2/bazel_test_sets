# Component: Effort Runner

## Location

`orchestrator/execution/effort.py`

## Purpose

SPRT-based rerun engine for converge and max effort modes. Reruns tests using the Sequential Probability Ratio Test to classify each test as true_pass, true_fail, flake, or undecided. When `target_hashes` are provided, SPRT evaluation pools evidence from prior sessions that share the same target hash, enabling cross-session evidence accumulation. All reruns are recorded to the status file for persistence.

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
        target_hashes: dict[str, str] | None = None,
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

## Cross-Session Evidence Pooling

When `target_hashes` is provided (a dict mapping test name to content hash):

1. **Prior evidence loading**: Before starting reruns, `_load_prior_evidence` queries the status file for history entries recorded with the same target hash via `get_same_hash_history`. This returns a (runs, passes) tuple that seeds the SPRT counters.

2. **Evidence accumulation**: A test that ran 5 times in a previous session and 10 times now can reach a decision based on all 15 data points, as long as the target hash hasn't changed between sessions.

3. **Hash recording**: Each rerun records the target hash in the history entry via `record_run(target_hash=...)`, enabling future sessions to find and pool this evidence.

When `target_hashes` is `None`, only session-local run data is used (backward compatible behavior).

## Mini-Converge Pattern

The EffortRunner infrastructure is reused by the regression flow as a "mini-converge" pattern. After regression-selected tests are executed, their results are processed through the lifecycle state machine (`process_results`), which handles stable demotion via SPRT evaluation on persisted history. This provides lifecycle-aware test evaluation without a dedicated converge phase.

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Provides test node metadata (executable paths)
- **Executor** (`orchestrator.execution.executor.TestResult`): Test result data type
- **SPRT** (`orchestrator.lifecycle.sprt.sprt_evaluate`): Core SPRT decision function
- **Status File** (`orchestrator.lifecycle.status.StatusFile`): Records reruns, provides config (min_reliability, statistical_significance), and supports `get_same_hash_history` for evidence pooling

## Dependents

- **Orchestrator Main** (`orchestrator.main._run_effort`): Creates EffortRunner and calls `run()` for converge/max effort modes

## Key Design Decisions

1. **Cross-session evidence pooling via target hashes**: When `target_hashes` is provided, SPRT evaluation pools evidence from prior sessions with matching hashes. This allows tests to accumulate evidence across CI runs for the same code state, reaching decisions faster. When hashes are unavailable or `target_hashes` is None, only session-local data is used (backward compatible).

2. **Round-robin iteration**: Each target test gets one rerun per round, ensuring fair budget distribution across tests. Tests are removed from the target set as soon as SPRT reaches a decision.

3. **Per-test budget**: Each test has its own budget of `max_reruns` reruns (not counting the initial run). When the budget is exhausted without SPRT convergence, the test is classified as `undecided`.

4. **Subprocess execution**: Uses the same subprocess pattern as BurnInSweep, running test executables directly with configurable timeout.
