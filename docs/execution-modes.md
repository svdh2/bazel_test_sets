# Execution Modes

The orchestrator supports two execution modes, each optimized for a
different use case. Either mode can be combined with the `--effort` flag
to control test execution thoroughness.

## Diagnostic Mode

**Purpose**: Run all tests in dependency order for complete diagnostics.

**Execution order**: Leaves-first (topological sort). Tests with no
dependencies run first, then tests whose dependencies have completed.

**Failure behavior**: When a test fails, all tests that depend on it are
marked `dependencies_failed` and skipped. Tests without dependency on the
failed test continue to run.

```bash
bazel run //path/to:my_tests -- --mode diagnostic
```

**When to use**:
- CI/CD pipeline full test runs
- Nightly validation suites
- When you need complete test coverage results

**Five-status model**: Diagnostic mode uses the full five-status model:

| Status | Meaning |
|--------|---------|
| `passed` | Test ran and succeeded |
| `failed` | Test ran and failed |
| `dependencies_failed` | Test was skipped because a dependency failed |
| `passed+dependencies_failed` | Test passed but some dependencies also failed (race condition in parallel execution) |
| `failed+dependencies_failed` | Test failed and some dependencies also failed |

## Detection Mode

**Purpose**: Fast failure detection with minimal test execution.

**Execution order**: Roots-first (BFS from roots). Tests at the top of the
DAG run first, working down toward leaves.

**Failure behavior**: Stops immediately when `--max-failures` threshold is
reached. Useful for quick feedback.

```bash
bazel run //path/to:my_tests -- --mode detection
```

**When to use**:
- Pre-commit hooks
- Pull request checks where fast feedback matters
- Developer workstation testing

## Effort Modes

The `--effort` flag controls test execution thoroughness. Each mode implies
a verdict computation level.

| Mode | Behavior | Verdict | Use case |
|------|----------|---------|----------|
| *(none)* | Run all tests once | none | Local dev, quick checks |
| `regression` | Co-occurrence test selection | quick | Feature branch CI |
| `converge` | Run all, SPRT-rerun failures | hifi | Merge-stage CI |
| `max` | Run all, SPRT-rerun everything | hifi | Release validation |

### Regression

Narrows test scope to tests correlated with changed files via co-occurrence
scoring. See [Regression](regression-mode.md) for full details.

```bash
bazel run //path/to:my_tests -- --mode diagnostic --effort regression --diff-base main
```

### Converge

Runs all tests once, then reruns only failed tests using SPRT until each is
classified as true_fail (genuine failure) or flake (intermittent). Passing
tests are classified as true_pass without reruns.

Requires `status_file` in `.test_set_config`.

```bash
bazel run //path/to:my_tests -- --effort converge
```

### Max

Runs all tests once, then reruns ALL tests (both passing and failing) using
SPRT. Provides the most thorough classification of every test.

Requires `status_file` in `.test_set_config`.

```bash
bazel run //path/to:my_tests -- --effort max
```

### SPRT Classification

For converge and max modes, each test is classified based on its initial
status and SPRT outcome:

| Initial status | SPRT accept (reliable) | SPRT reject (unreliable) | Budget exhausted |
|---|---|---|---|
| failed | flake | true_fail | undecided |
| passed | true_pass | flake | undecided |

Flakes cause exit code 1 (block CI).

## Parallel Execution

All modes support parallel execution via the `max_parallel` setting in
`.test_set_config`:

```json
{"max_parallel": 8}
```

The executor uses a sliding window with `asyncio.Semaphore` to control
concurrency. Tests are dispatched as their dependencies complete, maintaining
DAG ordering even in parallel.

## Report Generation

All modes support JSON report output. The `test_set` runner writes reports
to `target/reports/` automatically. You can also specify a custom path:

```bash
bazel run //path/to:my_tests -- --output results.json
```

The report includes:
- Summary statistics (total, passed, failed, duration)
- Per-test results with timing and logs
- Hierarchical structure mirroring the DAG
- Structured log data (if tests emit `[TST]` events)

Generate HTML from the JSON report:

```python
from orchestrator.reporting.html_reporter import generate_html_from_file
html = generate_html_from_file(Path("results.json"))
```
