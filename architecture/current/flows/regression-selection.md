# Flow: Regression Selection

## Purpose

Selects a subset of tests most likely to catch regressions for a given code change, then executes them in the chosen mode (diagnostic or detection). Reduces CI time for feature branch builds by running only relevant tests. When a status file is configured, integrates hash-based filtering and burn-in test inclusion.

## Trigger

```bash
bazel run //ci:pr -- --allow-dirty
```

Or directly via the orchestrator:

```bash
bazel run //path/to:my_test_set -- \
    --mode diagnostic \
    --effort regression \
    --diff-base main \
    --co-occurrence-graph .tests/co_occurrence_graph.json \
    --max-test-percentage 0.10 \
    --status-file .tests/status
```

Or with explicit changed files:

```bash
bazel run //path/to:my_test_set -- \
    --effort regression \
    --changed-files "src/payment.py,src/cart.py"
```

## Prerequisites

The co-occurrence graph must be built first:

```bash
bazel run //orchestrator:main -- build-graph --output .tests/co_occurrence_graph.json
```

## Steps

### 1. Hash Computation (when `--status-file` configured)

Compute target hashes for all tests via `bazel aquery`. Compare against stored hashes in the status file:
- **Changed hashes**: Invalidate SPRT evidence, update stored hash
- **Unchanged hashes**: Mark as skippable (if state is stable/flaky/disabled)

**Components**: Orchestrator Main (`_compute_and_filter_hashes`), Status File

### 2. Determine Changed Files

Either:
- Parse `--changed-files` comma-separated list
- Run `git diff --name-only {diff_base}...HEAD` via subprocess

**Components**: Orchestrator Main

### 3. Load Co-occurrence Graph

Load the JSON graph from `--co-occurrence-graph` path. If missing, exit with error directing user to run `build-graph`.

**Components**: Co-occurrence Graph (`load_graph`)

### 4. Co-occurrence Analysis

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

### 5. Test Selection

1. Sort candidates by score (descending)
2. Select top N, where N = `max_test_percentage * total_stable_tests`
3. Add dependency closure (transitive dependencies of selected tests)
4. If fewer than `min_tests` (3), fall back to all stable tests

**Components**: Regression Selector

### 6. Hash Intersection (when hash data available)

Intersect co-occurrence-selected tests with hash-changed tests. This removes tests that were selected by co-occurrence but whose target inputs haven't actually changed. Only applied when `--skip-unchanged` is enabled (default).

**Components**: Orchestrator Main

### 7. Burn-in Test Inclusion (when `--status-file` configured)

Add `new` and `burning_in` tests from the status file alongside co-occurrence-selected stable tests. These tests need evidence regardless of hash change status. The combined set gets dependency closure added.

**Components**: Orchestrator Main, Burn-in (`filter_tests_by_state`)

### 8. Manifest Filtering

Create a filtered manifest containing only selected tests. Prune `depends_on` edges to reference only selected tests.

**Components**: Orchestrator Main (`_filter_manifest`)

### 9. DAG Construction and Execution

Build a new `TestDAG` from the filtered manifest and execute using the chosen mode (diagnostic or detection) -- following the same execution flow as the non-regression case.

**Components**: DAG, Executor

### 10. Mini-Converge (Lifecycle Integration)

When a status file is configured, process results through the lifecycle state machine:
- Record each result with target hash via `process_results`
- Handle stable test demotions (SPRT evaluation on persisted history)
- Compute quick E-value verdict

**Components**: Burn-in (`process_results`), E-values

### 11. Output

Print selection summary:
```
Regression (diagnostic): 4 tests selected from 11 stable tests (2 files changed)
  (burn-in: 2 new/burning_in tests included)

Hash filter: 6 tests changed, 5 unchanged (3 skippable)
Hash filter removed 1 unchanged test from regression selection

Mode: diagnostic + regression
Tests executed: 5
  [PASS] cart - Shopping cart calculates totals (0.38s)
  [PASS] discount - Discount codes apply correctly (0.41s)
  [PASS] credit_card - Credit card payments authorized (0.42s)
  [PASS] new_feature - New feature test (0.31s) [burning_in]
  [PASS] order_placement - Full order completes (1.23s)

Results: 5 passed, 0 failed, 0 skipped
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
         |
         v
    Hash intersection (if target_hashes available)
         |
         v
    filtered_selected (unchanged tests removed)
         |
         v
    Burn-in inclusion (new + burning_in tests added)
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
         |
         v
    process_results() + quick verdict
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
| Hash computation failure | Warning, skip hash-based filtering |
