# Component: ci_gate Rule

## Location

`rules/ci_gate.bzl`

## Purpose

Bundles a `test_set` target with execution policy and lifecycle parameters into a single runnable Bazel target. Each CI stage (PR gate, merge gate, post-merge, release) is a `ci_gate` target that invokes the orchestrator with baked-in flags. This replaces the older pattern of passing parameters via `.test_set_config` files.

## Interface

### Starlark Macro

```python
ci_gate(
    name,                          # Target name (e.g., "pr_gate_tests")
    test_set,                      # Label of the test_set target to run
    mode = "diagnostic",           # "diagnostic" or "detection"
    effort = None,                 # "regression", "converge", "max", or None
    max_reruns = 100,              # Max SPRT reruns per test
    max_failures = None,           # Stop after N failures (detection mode)
    max_parallel = None,           # Max parallel test executions
    status_file = None,            # Path to status file (workspace-relative)
    diff_base = None,              # Git ref for regression diff
    co_occurrence_graph = None,    # Path to co-occurrence graph
    max_test_percentage = "0.10",  # Regression: max fraction of tests
    max_hops = 2,                  # Regression: max BFS hops
    skip_unchanged = True,         # Skip hash-unchanged tests
    min_reliability = "0.99",      # SPRT reliability threshold
    statistical_significance = "0.95",  # SPRT significance level
    flaky_deadline_days = 14,      # Days before flaky auto-disable
)
```

### Generated Runner Script

The rule generates a bash script that:
1. Locates the runfiles directory
2. Creates the output report directory (`target/reports/`)
3. Invokes the orchestrator with `--manifest`, `--output`, `--discover-workspace-tests`, and all baked-in flags
4. Passes through additional `"$@"` arguments for overrides

### Example Usage

```python
load("//rules:ci_gate.bzl", "ci_gate")

# PR gate: regression selection with mini-converge
ci_gate(
    name = "pr_gate",
    test_set = ":my_tests",
    effort = "regression",
    diff_base = "main",
    status_file = ".tests/status",
    co_occurrence_graph = ".tests/co_occurrence_graph.json",
)

# Post-merge: full converge with SPRT
ci_gate(
    name = "post_merge",
    test_set = ":my_tests",
    effort = "converge",
    status_file = ".tests/status",
)

# Release: max effort, all tests evaluated
ci_gate(
    name = "release_gate",
    test_set = ":my_tests",
    effort = "max",
    status_file = ".tests/status",
    skip_unchanged = False,
)
```

### Working Example: `examples/ci/BUILD.bazel`

The examples module defines three ci_gate targets (`pr`, `merge`, `staging`) wrapping backing test_set targets with distinct execution policies. These are invoked by the `./ci test-examples` command for the lifecycle demo.

## Dependencies

- **test_set rule** (`rules/test_set.bzl`): Provides `TestSetInfo` with the manifest file
- **Orchestrator Main** (`orchestrator/main.py`): The generated runner script invokes this

## Dependents

- **CI pipelines**: Each CI stage runs its `ci_gate` target via `bazel test` or `bazel run`

## Key Design Decisions

1. **Macro wrapping a rule**: The `ci_gate` macro wraps `_ci_gate_rule_test` (a `test = True` rule), ensuring Bazel naming conventions are met (names must end with `_test`). An alias is created if the user-provided name does not match.

2. **Float parameters as strings**: Starlark lacks native float support, so `min_reliability`, `statistical_significance`, and `max_test_percentage` are string attributes with format validation.

3. **Only non-default flags emitted**: The generated runner script only includes flags that differ from the orchestrator's defaults, keeping the script readable and reducing CLI noise.

4. **`$@` passthrough**: Additional CLI arguments can be passed at runtime for ad-hoc overrides (e.g., `bazel run :pr_gate -- --no-skip-unchanged`).

5. **Replaces `.test_set_config`**: Execution parameters are now embedded in the Bazel target rather than read from a separate config file. This provides better reproducibility (parameters are version-controlled in BUILD files) and supports per-stage configuration.
