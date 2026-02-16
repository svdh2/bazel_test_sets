# Component: Executor

## Location

`orchestrator/executor.py`

## Purpose

Executes tests according to the DAG ordering, propagating dependency failures and enforcing the max-failures threshold. Provides two implementations: `SequentialExecutor` for single-threaded execution and `AsyncExecutor` for sliding-window parallel execution using asyncio.

## Interface

### TestResult (dataclass)

```python
@dataclass
class TestResult:
    name: str
    assertion: str
    status: str       # passed, failed, dependencies_failed,
                      # passed+dependencies_failed, failed+dependencies_failed
    duration: float
    stdout: str
    stderr: str
    exit_code: int | None
```

### SequentialExecutor

```python
class SequentialExecutor:
    def __init__(self, dag, mode="diagnostic", max_failures=None, timeout=300.0)
    def execute(self) -> list[TestResult]
```

### AsyncExecutor

```python
class AsyncExecutor:
    def __init__(self, dag, mode="diagnostic", max_failures=None, max_parallel=None, timeout=300.0)
    def execute(self) -> list[TestResult]
```

### Five-Status Model

| Status | Meaning |
|--------|---------|
| `passed` | Test executed and returned exit code 0 |
| `failed` | Test executed and returned non-zero exit code (or timed out) |
| `dependencies_failed` | Test was not executed because a dependency failed |
| `passed+dependencies_failed` | Test passed but a dependency failed while it was running (race condition) |
| `failed+dependencies_failed` | Test failed and a dependency also failed while it was running |

## Dependencies

- **DAG** (`orchestrator.dag.TestDAG`): Provides ordering and dependency queries

## Dependents

- **Orchestrator Main**: Creates and invokes executors
- **Reporter**: Consumes `TestResult` objects for report generation
- **Burn-in**: Uses `TestResult` from its own test execution (similar pattern)

## Key Design Decisions

1. **Sliding window (not batching)**: The AsyncExecutor dispatches tests as soon as their dependencies are satisfied, rather than waiting for an entire "level" to complete. This uses an asyncio semaphore for concurrency control, achieving higher utilization.

2. **Combined statuses**: In parallel execution, a dependency can fail while a dependent is already running. The AsyncExecutor tracks start times and dependency failure times to produce combined statuses (`passed+dependencies_failed`, `failed+dependencies_failed`) rather than silently discarding the race condition.

3. **Mode-specific dependency gating**: In diagnostic mode, a test with a failed dependency is immediately marked `dependencies_failed` and skipped. In detection mode, tests run regardless of dependency status (for maximum coverage).

4. **Thread pool for subprocess**: The AsyncExecutor runs subprocess.run in a thread pool executor (`run_in_executor`) rather than using asyncio subprocess, avoiding child watcher issues in containerized environments.

5. **Timeout and error handling**: Tests that time out, have missing executables, or hit OS errors are all caught and reported as `failed` with descriptive stderr messages.
