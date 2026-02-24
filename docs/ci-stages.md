# CI Stages

The `ci_gate` rule bundles a `test_set` target with execution policy and
lifecycle parameters into a single runnable Bazel target. Each CI stage
(PR gate, merge gate, post-merge, release) is a `ci_gate` target that
invokes the orchestrator with baked-in flags.

## Quick Start

```python
load("//rules:ci_gate.bzl", "ci_gate")

# Define your test set
test_set(
    name = "my_tests",
    tests = [":test_a", ":test_b", ":test_c"],
)

# PR gate: regression selection + hash filtering
ci_gate(
    name = "pr_gate",
    test_set = ":my_tests",
    effort = "regression",
    diff_base = "main",
    status_file = ".tests/status",
    co_occurrence_graph = ".tests/co_occurrence_graph.json",
)

# Post-merge: converge with SPRT reruns on failures
ci_gate(
    name = "post_merge",
    test_set = ":my_tests",
    effort = "converge",
    status_file = ".tests/status",
)

# Release: max effort, evaluate everything
ci_gate(
    name = "release_gate",
    test_set = ":my_tests",
    effort = "max",
    status_file = ".tests/status",
    skip_unchanged = False,
)
```

Run a CI stage:

```bash
bazel run //ci:pr_gate
bazel run //ci:post_merge
bazel run //ci:release_gate
```

## CI Stage Configuration

### PR Gate (Feature Branch)

| Parameter | Recommended | Purpose |
|-----------|-------------|---------|
| `effort` | `"regression"` | Only run tests correlated with changed files |
| `diff_base` | `"main"` | Compare against main branch |
| `status_file` | `".tests/status"` | Enable lifecycle tracking |
| `co_occurrence_graph` | `".tests/co_occurrence_graph.json"` | Required for regression selection |
| `skip_unchanged` | `True` (default) | Skip tests with unchanged target inputs |
| `max_test_percentage` | `"0.10"` (default) | Select top 10% of correlated tests |

The PR gate uses regression selection to run only tests likely affected by the
code change. Hash-based filtering further reduces the set by skipping tests
whose target inputs (source, data, dependencies) haven't changed. New and
burning-in tests are always included regardless of co-occurrence scores.

### Merge Gate (Post-PR, Pre-Merge)

| Parameter | Recommended | Purpose |
|-----------|-------------|---------|
| `effort` | `"converge"` | Rerun failed tests with SPRT |
| `status_file` | `".tests/status"` | Enable lifecycle tracking |
| `max_reruns` | `100` (default) | SPRT budget per test |

The merge gate runs all tests, then uses SPRT to classify failures as genuine
(`true_fail`) or intermittent (`flake`). Flakes block the merge because they
indicate unreliable tests that need attention.

### Post-Merge

| Parameter | Recommended | Purpose |
|-----------|-------------|---------|
| `effort` | `"converge"` | Same as merge gate |
| `status_file` | `".tests/status"` | Enable lifecycle tracking |

Same configuration as the merge gate. Runs after the merge to confirm the
combined code is stable. Burn-in sweep runs automatically for any
`burning_in` tests.

### Release Gate

| Parameter | Recommended | Purpose |
|-----------|-------------|---------|
| `effort` | `"max"` | SPRT-rerun all tests |
| `status_file` | `".tests/status"` | Enable lifecycle tracking |
| `skip_unchanged` | `False` | Evaluate all tests regardless of hash |
| `max_reruns` | `100` (default) | SPRT budget per test |

The release gate provides the most thorough evaluation. Every test is
SPRT-classified, not just failures. Hash-based skipping is disabled to
ensure complete coverage.

## Hash-Based Filtering

When `status_file` is configured, the orchestrator computes a content hash
for each test target using `bazel aquery`. This hash covers the test's source
files, data dependencies, and transitive dependencies.

**How it works:**

1. On first run, all test hashes are stored in the status file
2. On subsequent runs, each hash is compared against the stored value
3. Changed hashes: SPRT evidence is invalidated, test re-enters burn-in
4. Unchanged hashes with conclusive state: test can be skipped

**Benefits:**

- PR gates skip tests whose inputs haven't changed
- Evidence accumulates across CI runs for the same code state
- Code changes automatically invalidate stale evidence

**Control:**

- `skip_unchanged = True` (default): Skip tests with unchanged hashes and
  conclusive SPRT state
- `skip_unchanged = False`: Compute hashes for evidence pooling but don't
  skip any tests
- Release gates should use `skip_unchanged = False` for complete coverage

## Flake Handling Progression

Tests progress through lifecycle states as the system evaluates their
reliability:

| State | Meaning | How tests enter | How tests leave |
|-------|---------|-----------------|-----------------|
| `new` | Recently added, not yet evaluated | First appearance; re-enabled from disabled | `burn-in` subcommand -> `burning_in` |
| `burning_in` | Under SPRT evaluation | From `new` via burn-in; from `stable` via suspicious failure; from `flaky` via deflake | SPRT accept -> `stable`; SPRT reject -> `flaky` |
| `stable` | Confirmed reliable | SPRT accept during burn-in | Failed + demotion SPRT -> `flaky`; inconclusive -> `burning_in` |
| `flaky` | Confirmed unreliable | SPRT reject during burn-in; demotion from stable | Deflake -> `burning_in`; deadline -> `disabled` |
| `disabled` | Excluded from execution | BUILD `disabled=True`; flaky deadline | BUILD re-enabled -> `new` |

## Flaky Test Resolution Workflow

When a test is identified as flaky:

1. **Detection**: Test is classified as `flaky` by SPRT during burn-in or
   by demotion evaluation after stable failure

2. **Investigation**: Use `test-status` to see the test's history:
   ```bash
   bazel run //orchestrator:main -- test-status \
       --status-file .tests/status --state flaky
   ```

3. **Fix**: Address the root cause of flakiness (timing issues, shared
   state, external dependencies, etc.)

4. **Re-promote**: Use `deflake` to restart the burn-in cycle:
   ```bash
   bazel run //orchestrator:main -- deflake \
       --status-file .tests/status //test:flaky_test
   ```
   This transitions the test to `burning_in` with cleared history and
   cleared `target_hash`, giving it a fresh evaluation.

5. **Auto-disable** (optional): If a flaky test isn't fixed within the
   deadline period (default 14 days), it is automatically transitioned
   to `disabled`. Configure the deadline via `flaky_deadline_days`:
   ```python
   ci_gate(
       name = "pr_gate",
       test_set = ":my_tests",
       flaky_deadline_days = 14,   # Auto-disable after 14 days
       # ...
   )
   ```
   Set to a negative value to disable the deadline entirely.

## SPRT Parameters

The SPRT (Sequential Probability Ratio Test) parameters control how
reliability is evaluated:

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `min_reliability` | `"0.99"` | Minimum pass rate for a test to be considered reliable |
| `statistical_significance` | `"0.95"` | Confidence level for SPRT decisions |
| `max_reruns` | `100` | Maximum reruns before classifying as undecided |

These are set per `ci_gate` target. More stringent thresholds require
more runs to reach a decision but provide stronger guarantees.

## Example: Full CI Pipeline

```python
load("//rules:ci_gate.bzl", "ci_gate")
load("//rules:test_set.bzl", "test_set")

test_set(
    name = "all_tests",
    tests = glob(["*_test"]),
)

# Feature branch: fast, targeted
ci_gate(
    name = "pr_gate",
    test_set = ":all_tests",
    effort = "regression",
    diff_base = "main",
    status_file = ".tests/status",
    co_occurrence_graph = ".tests/co_occurrence_graph.json",
    max_test_percentage = "0.15",
    flaky_deadline_days = 7,
)

# Merge gate: thorough on failures
ci_gate(
    name = "merge_gate",
    test_set = ":all_tests",
    effort = "converge",
    status_file = ".tests/status",
    flaky_deadline_days = 14,
)

# Release: exhaustive
ci_gate(
    name = "release_gate",
    test_set = ":all_tests",
    effort = "max",
    status_file = ".tests/status",
    skip_unchanged = False,
    min_reliability = "0.999",
    flaky_deadline_days = -1,  # No auto-disable for release
)
```
