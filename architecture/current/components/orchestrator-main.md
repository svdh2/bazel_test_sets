# Component: Orchestrator Main

## Location

`orchestrator/main.py`

## Purpose

CLI entry point for the test set orchestrator and lifecycle management. Parses command-line arguments, loads the JSON manifest, builds the test DAG, and dispatches execution to the appropriate executor. Supports diagnostic and detection modes, with an optional `--effort` flag that controls test execution thoroughness: regression (co-occurrence selection + quick verdict), converge (SPRT reruns on failures + hifi verdict), or max (SPRT reruns on all tests + hifi verdict). Also provides lifecycle subcommands for burn-in, deflake, test-status, re-judge, and build-graph.

## Interface

### CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--manifest` | Path | Required (run mode) | Path to the JSON manifest file |
| `--mode` | Choice | `diagnostic` | Execution mode: `diagnostic` or `detection` |
| `--effort` | Choice | None | Effort mode: `regression`, `converge`, or `max` |
| `--output` | Path | None | Path for JSON report output |
| `--config-file` | Path | None | Path to `.test_set_config` JSON file |
| `--allow-dirty` | flag | false | Allow running with uncommitted changes |
| `--diff-base` | string | None | Git ref for regression diff (e.g., `main`) |
| `--changed-files` | string | None | Comma-separated changed files (alternative to `--diff-base`) |
| `--co-occurrence-graph` | Path | `.tests/co_occurrence_graph.json` | Co-occurrence graph path |

Execution tuning parameters and the status file path are read from `.test_set_config` (see [Test Set Config](test-set-config.md)).

### Lifecycle Subcommands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `burn-in` | Transition tests from `new` to `burning_in` | `--status-file`, positional test names |
| `deflake` | Transition `flaky` tests back to `burning_in` with reset counters | `--status-file`, positional test names (required) |
| `test-status` | Display tabular status of all tests | `--status-file`, `--state` filter |
| `re-judge` | Re-evaluate judgements using stored measurements | `--manifest`, `--measurements-dir`, positional test names |
| `build-graph` | Build/update co-occurrence graph from git history | `--output`, `--source-extensions`, `--test-patterns`, `--max-history`, `--repo-root` |

### Public Function

```python
def main(argv: list[str] | None = None) -> int:
```

Returns exit code 0 if all tests pass, 1 if any test fails.

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Builds the test graph from manifest
- **Executor** (`orchestrator.execution.executor`): `SequentialExecutor` and `AsyncExecutor` for test execution
- **Reporter** (`orchestrator.reporting.reporter.Reporter`): Generates JSON reports
- **HTML Reporter** (`orchestrator.reporting.html_reporter`): Generates HTML reports
- **Effort Runner** (`orchestrator.execution.effort.EffortRunner`): SPRT-based rerun engine for converge/max modes (lazy import)
- **Co-occurrence** (`orchestrator.regression.co_occurrence`): Loads co-occurrence graph (lazy import for `--effort regression`)
- **Regression Selector** (`orchestrator.regression.regression_selector`): Selects tests for regression runs (lazy import)
- **E-values** (`orchestrator.lifecycle.e_values`): Computes test set verdict (lazy import when effort mode implies a verdict)
- **Status File** (`orchestrator.lifecycle.status.StatusFile`): Used by lifecycle subcommands (burn-in, deflake, test-status)
- **Judgement** (`orchestrator.analysis.judgement`): Used by re-judge subcommand (lazy import)
- **Co-occurrence (build-graph)** (`orchestrator.regression.co_occurrence`): Used by build-graph subcommand (lazy import)

## Dependents

- **Bazel**: test_set rules invoke `orchestrator/main.py` at run time (during `bazel test` or `bazel run`). Lifecycle subcommands are invoked via `bazel run //orchestrator:main -- <subcommand>`. Bazel is triggered by the CI Script inside the container.

## Key Design Decisions

1. **Lazy imports for effort modes**: Co-occurrence, regression selector, effort runner, and E-value modules are imported only when the corresponding `--effort` mode is used, keeping the default path lightweight.

2. **Executor selection**: `max_parallel == 1` uses SequentialExecutor (simpler, no asyncio overhead); all other values use AsyncExecutor with a semaphore-based sliding window.

3. **Manifest filtering for regression**: When `--effort regression` selects a subset of tests, the manifest is filtered to include only selected tests, with depends_on edges pruned to the selected set. A new DAG is then built from the filtered manifest.

4. **Dual report output**: When `--output` is specified, both JSON and HTML reports are written (same path, different extensions).

5. **Verdict mode derived from effort**: The verdict mode is determined by the `--effort` flag rather than a separate CLI argument. No effort = no verdict, regression = quick verdict, converge/max = hifi verdict. Uses default alpha_set=0.05, beta_set=0.05.

6. **Effort mode dispatch**: `--effort converge` reruns only failed tests via SPRT; `--effort max` reruns all tests. Both require `status_file` in `.test_set_config` and git context. The EffortRunner classifies each test as true_pass, true_fail, flake, or undecided. Flakes cause exit code 1 (block CI).
