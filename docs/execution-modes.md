# Execution Modes

The orchestrator supports two execution modes, each optimized for a
different use case. Either mode can be combined with the `--regression` flag
to narrow the test scope.

## Diagnostic Mode

**Purpose**: Run all tests in dependency order for complete diagnostics.

**Execution order**: Leaves-first (topological sort). Tests with no
dependencies run first, then tests whose dependencies have completed.

**Failure behavior**: When a test fails, all tests that depend on it are
marked `dependencies_failed` and skipped. Tests without dependency on the
failed test continue to run.

```bash
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode diagnostic
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
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode detection \
    --max-failures 3
```

**When to use**:
- Pre-commit hooks
- Pull request checks where fast feedback matters
- Developer workstation testing

## Regression Flag

Either mode can be combined with `--regression` to run only tests correlated
with changed files. Co-occurrence scoring analyses git history to select the
most relevant subset.

**Selection algorithm**:
1. Identify changed files (via `--diff-base` or `--changed-files`)
2. Load co-occurrence graph
3. Find tests that historically co-occurred with changed files
4. Score tests by frequency, recency, and hop distance
5. Select top tests up to `--max-test-percentage` of stable tests
6. Add dependency closure for self-containment

```bash
# Diagnostic mode with regression selection (using git diff)
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode diagnostic \
    --regression \
    --diff-base main

# Detection mode with regression selection (using explicit file list)
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode detection \
    --regression \
    --changed-files "src/auth.py,src/payment.py"
```

**When to use**:
- Feature branch CI where running all tests is too slow
- Large test suites where only a subset is relevant
- Post-commit testing with targeted coverage

See [Regression](regression-mode.md) for full details.

## Parallel Execution

All modes support parallel execution via `--max-parallel`:

```bash
# Run up to 8 tests simultaneously
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode diagnostic \
    --max-parallel 8

# Sequential execution (for debugging)
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --max-parallel 1
```

The executor uses a sliding window with `asyncio.Semaphore` to control
concurrency. Tests are dispatched as their dependencies complete, maintaining
DAG ordering even in parallel.

## Report Generation

All modes support JSON report output:

```bash
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --output results.json
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
