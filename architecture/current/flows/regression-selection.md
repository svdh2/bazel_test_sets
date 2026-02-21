# Flow: Regression Selection

## Purpose

Selects a subset of tests most likely to catch regressions for a given code change, then executes them in the chosen mode (diagnostic or detection). Reduces CI time for feature branch builds by running only relevant tests.

## Trigger

```bash
bazel run //path/to:my_test_set -- \
    --mode diagnostic \
    --regression \
    --diff-base main \
    --co-occurrence-graph .tests/co_occurrence_graph.json \
    --max-test-percentage 0.10
```

Or with explicit changed files:

```bash
bazel run //path/to:my_test_set -- \
    --regression \
    --changed-files "src/payment.py,src/cart.py"
```

## Prerequisites

The co-occurrence graph must be built first:

```bash
bazel run //orchestrator:main -- build-graph --output .tests/co_occurrence_graph.json
```

## Steps

### 1. Determine Changed Files

Either:
- Parse `--changed-files` comma-separated list
- Run `git diff --name-only {diff_base}...HEAD` via subprocess

**Components**: Orchestrator Main

### 2. Load Co-occurrence Graph

Load the JSON graph from `--co-occurrence-graph` path. If missing, exit with error directing user to run `build-graph`.

**Components**: Co-occurrence Graph (`load_graph`)

### 3. Co-occurrence Analysis

The regression selector performs bounded BFS expansion:

```
Changed files (seed)
    |
    v
[Hop 0] Find commits touching changed files
    |     Score co-occurring test files: score += 1.0 * recency
    |     Discover new source files
    |
    v
[Hop 1] Find commits touching discovered files
    |     Score: score += 0.5 * recency (decay)
    |     Discover more source files
    |
    v
[Hop 2] Find commits touching discovered files
          Score: score += 0.25 * recency (more decay)
```

Each test receives a cumulative score based on:
- **Frequency**: How many commits show the test co-occurring with changed files
- **Recency**: Exponential decay with 180-day half-life
- **Proximity**: Hop decay (0.5 per hop)

**Components**: Regression Selector

### 4. Test Selection

1. Sort candidates by score (descending)
2. Select top N, where N = `max_test_percentage * total_stable_tests`
3. Add dependency closure (transitive dependencies of selected tests)
4. If fewer than `min_tests` (3), fall back to all stable tests

**Components**: Regression Selector

### 5. Manifest Filtering

Create a filtered manifest containing only selected tests. Prune `depends_on` edges to reference only selected tests.

**Components**: Orchestrator Main (`_filter_manifest`)

### 6. DAG Construction and Execution

Build a new `TestDAG` from the filtered manifest and execute using the chosen mode (diagnostic or detection) -- following the same execution flow as the non-regression case.

**Components**: DAG, Executor

### 7. Output

Print selection summary:
```
Regression (diagnostic): 4 tests selected from 11 stable tests (2 files changed)

Mode: diagnostic + regression
Tests executed: 4
  [PASS] cart - Shopping cart calculates totals (0.38s)
  [PASS] discount - Discount codes apply correctly (0.41s)
  [PASS] credit_card - Credit card payments authorized (0.42s)
  [PASS] order_placement - Full order completes (1.23s)

Results: 4 passed, 0 failed, 0 skipped
```

## Data Flow

```
--diff-base main
    |
    v
git diff --name-only main...HEAD
    |
    v
["src/cart.py", "src/discount.py"]  (changed files)
    |
    +---> co_occurrence_graph.json
    |         |
    v         v
    select_regression_tests()
         |
         v
    SelectionResult
         |
         +---> selected_tests: ["cart_wrapped", "discount_wrapped", ...]
         +---> scores: {"cart_wrapped": 2.3, ...}
         +---> selection_reason: {"cart_wrapped": "co-occurrence hop 0", ...}
         |
         v
    _filter_manifest(manifest, selected_tests)
         |
         v
    filtered_manifest
         |
         v
    TestDAG.from_manifest(filtered_manifest)
         |
         v
    Executor.execute()
         |
         v
    list[TestResult]
```

## Scoring Example

Given a change to `src/payment.py`:

| Test | Score | Reason |
|------|-------|--------|
| `credit_card_wrapped` | 2.8 | 3 commits at hop 0, recent |
| `paypal_wrapped` | 1.4 | 2 commits at hop 0, older |
| `order_placement_wrapped` | 0.6 | 1 commit at hop 1, recent |
| `cart_wrapped` | 0.3 | 1 commit at hop 2, old |
| `inventory_wrapped` | 0.0 | dependency closure |

## Error Handling

| Error | Behavior |
|-------|----------|
| No `--diff-base` or `--changed-files` | Exit 1, error message |
| Graph file missing | Exit 1, "run build-graph first" |
| No changed files detected | Exit 0, "no tests to run" |
| No tests selected | Exit 0, "no tests selected" |
| Fallback triggered | Warning printed, all tests run |
