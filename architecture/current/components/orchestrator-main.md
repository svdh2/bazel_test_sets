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
| `--allow-dirty` | flag | false | Allow running with uncommitted changes |
| `--discover-workspace-tests` | flag | false | Discover all `test_set_test` targets in the workspace via `bazel query` and include them in the report as `not_run` |
| `--max-reruns` | int | 100 | Maximum SPRT reruns per test |
| `--max-failures` | int | None | Stop after N failures (detection mode) |
| `--max-parallel` | int | None | Maximum parallel test executions |
| `--status-file` | Path | None | Path to the status file (enables lifecycle features) |
| `--max-test-percentage` | float | 0.10 | Regression: max fraction of tests to select |
| `--max-hops` | int | 2 | Regression: max BFS hops in co-occurrence graph |
| `--skip-unchanged` / `--no-skip-unchanged` | flag | true | Skip hash-unchanged tests with conclusive SPRT |
| `--min-reliability` | float | 0.99 | SPRT: minimum reliability threshold |
| `--statistical-significance` | float | 0.95 | SPRT: significance level for decisions |
| `--flaky-deadline-days` | int | 14 | Days before flaky tests auto-disable |
| `--diff-base` | string | None | Git ref for regression diff (e.g., `main`) |
| `--changed-files` | string | None | Comma-separated changed files (alternative to `--diff-base`) |
| `--co-occurrence-graph` | Path | `.tests/co_occurrence_graph.json` | Co-occurrence graph path |

All execution tuning parameters are passed as CLI flags, typically baked into a [ci_gate](ci-gate-rule.md) runner script. Statistical parameters (`--min-reliability`, `--statistical-significance`) are passed to the `StatusFile` constructor.

### Lifecycle Subcommands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `burn-in` | Transition tests from `new` to `burning_in` | `--status-file`, positional test names |
| `deflake` | Transition `flaky` tests back to `burning_in` with reset counters and cleared `target_hash` | `--status-file`, positional test names (required) |
| `test-status` | Display tabular status of all tests | `--status-file`, `--state` filter |
| `re-judge` | Re-evaluate judgements using stored measurements | `--manifest`, `--measurements-dir`, positional test names |
| `build-graph` | Build/update co-occurrence graph from git history | `--output`, `--source-extensions`, `--test-patterns`, `--max-history`, `--repo-root` |

### Public Function

```python
def main(argv: list[str] | None = None) -> int:
```

Returns exit code 0 if all tests pass, 1 if any test fails or any flake is detected (lifecycle-aware).

## Hash Computation and Filtering

When `--status-file` is configured, the orchestrator computes target hashes for all tests using `bazel aquery` (via `_compute_and_filter_hashes`). This enables:

1. **Evidence invalidation**: When a test's target hash changes, accumulated SPRT evidence is invalidated and the test transitions to `burning_in`.
2. **Unchanged test skipping**: Tests with unchanged hashes and a conclusive state (`stable`, `flaky`, `disabled`) can be skipped entirely (when `--skip-unchanged` is enabled).
3. **Cross-session evidence pooling**: Target hashes are stored in history entries, allowing SPRT evidence to accumulate across CI runs for the same code state.

The hash filter results are included in reports via the `hash_filter` section.

## Multi-Phase Execution

### Default Mode (no `--effort`)

Single-phase: run all tests once in the chosen mode (diagnostic or detection), then report results.

### Regression Mode (`--effort regression`)

1. **Hash computation**: Compute target hashes, invalidate changed evidence, identify skippable tests
2. **Co-occurrence selection**: Select tests correlated with changed files
3. **Hash intersection**: Intersect co-occurrence-selected tests with hash-changed tests (removes tests selected by co-occurrence but whose inputs haven't changed)
4. **Burn-in inclusion**: Add `new` and `burning_in` tests from the status file
5. **Execution**: Run selected tests in the chosen mode
6. **Mini-converge**: Process results through the lifecycle state machine, including stable demotion handling
7. **Quick verdict**: Compute E-value verdict

### Effort Mode (`--effort converge` or `--effort max`)

1. **Hash filtering**: Compute hashes and optionally skip unchanged tests
2. **Phase 1 (initial execution)**: Run all (non-skipped) tests once
3. **Phase 2 (SPRT rerun loop)**: EffortRunner reruns target tests with cross-session evidence pooling
4. **Phase 3 (burn-in sweep)**: If burning_in tests exist, run a BurnInSweep to decide them
5. **Phase 4 (verdict)**: Compute hifi E-value verdict
6. **Output**: Report with effort classifications, sweep results, and hash filter data

## Lifecycle-Aware Exit Codes

The exit code reflects both test failures and lifecycle state:

- Exit 0: All tests passed (or classified as `true_pass`)
- Exit 1: Any test failed, any test classified as `flake`, or any stable test demoted to `flaky`

Flakes block CI because they indicate unreliable tests that need attention.

## Dependencies

- **DAG** (`orchestrator.execution.dag.TestDAG`): Builds the test graph from manifest
- **Executor** (`orchestrator.execution.executor`): `SequentialExecutor` and `AsyncExecutor` for test execution
- **Reporter** (`orchestrator.reporting.reporter.Reporter`): Generates JSON reports
- **HTML Reporter** (`orchestrator.reporting.html_reporter`): Generates HTML reports
- **Effort Runner** (`orchestrator.execution.effort.EffortRunner`): SPRT-based rerun engine for converge/max modes (lazy import)
- **Burn-in** (`orchestrator.lifecycle.burnin`): `sync_disabled_state`, `check_flaky_deadlines`, `process_results`, `BurnInSweep`, `filter_tests_by_state` (lazy import)
- **Co-occurrence** (`orchestrator.regression.co_occurrence`): Loads co-occurrence graph (lazy import for `--effort regression`)
- **Regression Selector** (`orchestrator.regression.regression_selector`): Selects tests for regression runs (lazy import)
- **E-values** (`orchestrator.lifecycle.e_values`): Computes test set verdict (lazy import when effort mode implies a verdict)
- **Status File** (`orchestrator.lifecycle.status.StatusFile`): Lifecycle state persistence; receives `min_reliability` and `statistical_significance` directly via constructor
- **Target Hash** (`orchestrator.execution.target_hash`): Computes content hashes via `bazel aquery` (lazy import)
- **Judgement** (`orchestrator.analysis.judgement`): Used by re-judge subcommand (lazy import)
- **Co-occurrence (build-graph)** (`orchestrator.regression.co_occurrence`): Used by build-graph subcommand (lazy import)
- **Workspace Discovery** (`orchestrator.discovery.workspace`): Discovers workspace tests and test_sets via `bazel query` (lazy import when `--discover-workspace-tests` is passed)

## Dependents

- **ci_gate rule** (`rules/ci_gate.bzl`): The generated runner script invokes `orchestrator/main.py` with baked-in flags from the Starlark attributes
- **Bazel**: Lifecycle subcommands are invoked via `bazel run //orchestrator:main -- <subcommand>`. Bazel is triggered by the CI Script inside the container.

## Key Design Decisions

1. **Lazy imports for effort modes**: Co-occurrence, regression selector, effort runner, E-value, burn-in, and target hash modules are imported only when the corresponding `--effort` mode is used, keeping the default path lightweight.

2. **Executor selection**: `max_parallel == 1` uses SequentialExecutor (simpler, no asyncio overhead); all other values use AsyncExecutor with a semaphore-based sliding window.

3. **Manifest filtering for regression**: When `--effort regression` selects a subset of tests, the manifest is filtered to include only selected tests, with depends_on edges pruned to the selected set. A new DAG is then built from the filtered manifest.

4. **Dual report output**: When `--output` is specified, both JSON and HTML reports are written (same path, different extensions).

5. **Verdict mode derived from effort**: The verdict mode is determined by the `--effort` flag rather than a separate CLI argument. No effort = no verdict, regression = quick verdict, converge/max = hifi verdict. Uses default alpha_set=0.05, beta_set=0.05.

6. **Effort mode dispatch**: `--effort converge` reruns only failed tests via SPRT; `--effort max` reruns all tests. Both require `--status-file` and git context. The EffortRunner classifies each test as true_pass, true_fail, flake, or undecided. Flakes cause exit code 1 (block CI).

7. **Workspace discovery is reporting-only**: When `--discover-workspace-tests` is passed, `bazel query` discovers both `test_set_test` and `_test_set_rule_test` targets after test execution completes. Discovered tests and their hierarchical test_set structure are merged into a copy of the manifest for report generation, preserving the full DAG layout as defined in BUILD files. The execution DAG (built earlier from the original manifest) is never affected. Discovery gracefully degrades: if `BUILD_WORKSPACE_DIRECTORY` is not set, `bazel` is not found, or the query fails, the report is generated without workspace tests.

8. **CLI flags replace config file**: All execution tuning parameters (max_reruns, max_parallel, min_reliability, etc.) are passed as CLI flags rather than read from a `.test_set_config` file. The `ci_gate` Starlark rule bakes these values into the generated runner script, providing per-target configuration with full Bazel reproducibility.

9. **Flaky deadline auto-disable**: At orchestrator startup (when `--status-file` is configured), `check_flaky_deadlines` scans for flaky tests that have exceeded `--flaky-deadline-days`. Expired tests are transitioned to `disabled` state. A negative deadline value disables the check entirely (no deadline).

10. **Deflake clears target_hash**: The `deflake` subcommand resets history and clears the stored `target_hash` so the re-promoted test gets fresh hash tracking when it next runs.
