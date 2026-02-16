# Component: Orchestrator Main

## Location

`orchestrator/main.py`

## Purpose

CLI entry point for the test set orchestrator. Parses command-line arguments, loads the JSON manifest, builds the test DAG, and dispatches execution to the appropriate executor. Supports diagnostic and detection modes, with an optional regression flag to filter tests by co-occurrence analysis before execution.

## Interface

### CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--manifest` | Path | Required | Path to the JSON manifest file |
| `--mode` | Choice | `diagnostic` | Execution mode: `diagnostic` or `detection` |
| `--regression` | Flag | `false` | Enable regression test selection |
| `--max-parallel` | int | CPU count | Maximum parallel test executions |
| `--max-failures` | int | Unlimited | Stop after N failures |
| `--output` | Path | None | Path for YAML report output |
| `--status-file` | Path | None | Path to burn-in status file |
| `--diff-base` | string | None | Git ref for regression diff (e.g., `main`) |
| `--changed-files` | string | None | Comma-separated changed files (alternative to `--diff-base`) |
| `--co-occurrence-graph` | Path | `.tests/co_occurrence_graph.json` | Co-occurrence graph path |
| `--max-test-percentage` | float | `0.10` | Max fraction of stable tests for regression |
| `--max-hops` | int | `2` | Max BFS hops in co-occurrence expansion |

### Public Function

```python
def main(argv: list[str] | None = None) -> int:
```

Returns exit code 0 if all tests pass, 1 if any test fails.

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Builds the test graph from manifest
- **Executor** (`orchestrator.execution.executor`): `SequentialExecutor` and `AsyncExecutor` for test execution
- **Reporter** (`orchestrator.reporting.reporter.Reporter`): Generates YAML reports
- **HTML Reporter** (`orchestrator.reporting.html_reporter`): Generates HTML reports
- **Co-occurrence** (`orchestrator.regression.co_occurrence`): Loads co-occurrence graph (lazy import for regression)
- **Regression Selector** (`orchestrator.regression.regression_selector`): Selects tests for regression runs (lazy import)

## Dependents

- **Bazel**: test_set rules invoke `orchestrator/main.py` at run time (during `bazel test` or `bazel run`). Bazel is triggered by the CI Script inside the container. Note: `main.py` remains at the top level of the orchestrator package.

## Key Design Decisions

1. **Lazy imports for regression**: Co-occurrence and regression selector modules are imported only when `--regression` is used, keeping the default path lightweight.

2. **Executor selection**: `max_parallel == 1` uses SequentialExecutor (simpler, no asyncio overhead); all other values use AsyncExecutor with a semaphore-based sliding window.

3. **Manifest filtering for regression**: When regression mode selects a subset of tests, the manifest is filtered to include only selected tests, with depends_on edges pruned to the selected set. A new DAG is then built from the filtered manifest.

4. **Dual report output**: When `--output` is specified, both YAML and HTML reports are written (same path, different extensions).
