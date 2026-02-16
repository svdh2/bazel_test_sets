# API Reference

Complete reference for all Bazel rules, macros, and CLI commands.

## Bazel Rules

### test_set_test

Wraps an existing test target with DAG metadata for orchestrated execution.

```starlark
load("//rules:test_set_test.bzl", "test_set_test")

test_set_test(
    name = "my_test_wrapped",
    test = ":my_raw_test",
    assertion = "Feature X works correctly",
    requirement_id = "REQ-001",
    depends_on = [":dependency_wrapped"],
    judgement = ":my_judgement_target",
)
```

| Attribute | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | yes | - | Target name (Bazel requires names ending in `_test` for test rules) |
| `test` | label | yes | - | The wrapped test target (`py_test`, `cc_test`, etc.) |
| `assertion` | string | yes | - | Human-readable description of what this test verifies |
| `requirement_id` | string | no | `""` | Traceability ID linking to a requirements document |
| `depends_on` | label_list | no | `[]` | Other `test_set_test` targets this test depends on (DAG edges) |
| `judgement` | label | no | `None` | Separate judgement target for deferred evaluation |

**Providers**: Returns `TestSetTestInfo` with fields: `test_label`, `assertion`,
`requirement_id`, `depends_on`, `judgement_label`, `executable`.

### test_set

Declares a test set that groups tests and subsets into a hierarchical DAG.
Generates a JSON manifest file for orchestrator consumption.

```starlark
load("//rules:test_set.bzl", "test_set")

test_set(
    name = "checkout_tests",
    tests = [":payment_wrapped", ":cart_wrapped"],
    subsets = [":payment_method_tests"],
    assertion = "Checkout flow works end-to-end",
    requirement_id = "CHECKOUT-001",
)
```

| Attribute | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | yes | - | Target name |
| `tests` | label_list | no | `[]` | Direct `test_set_test` targets in this set |
| `subsets` | label_list | no | `[]` | Child `test_set` targets (for nesting) |
| `assertion` | string | yes | - | Description of what this test set verifies |
| `requirement_id` | string | no | `""` | Traceability ID |

**Providers**: Returns `TestSetInfo` with fields: `name`, `assertion`,
`requirement_id`, `test_labels`, `subset_labels`, `all_tests`.

**Output**: Generates `<name>_manifest.json` containing the full DAG structure.

## Macros

### matrix_test_set

Generates a test set from a matrix of parameter combinations. Each entry
produces a concrete `py_test` + `test_set_test` pair.

```starlark
load("//macros:matrix_test_set.bzl", "matrix_test_set")

matrix_test_set(
    name = "payment_regions",
    test_src = "payment_region_test.py",
    assertion_template = "Payment works in {region} with {currency}",
    matrix = {
        "us": {"region": "US", "currency": "USD"},
        "eu": {"region": "EU", "currency": "EUR"},
    },
    assertion = "Payment works in all regions",
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | Base name for the generated test set |
| `test_src` | string | yes | Source file shared across all combinations |
| `assertion_template` | string | yes | Python format string with `{param}` placeholders |
| `matrix` | dict | yes | `{variant_name: {param: value, ...}}` |

Parameters are passed as `--key=value` command-line arguments to the test.

### parameterized_test_set

Generates a test set from named variants with individual assertions and
environment variables.

```starlark
load("//macros:parameterized_test_set.bzl", "parameterized_test_set")

parameterized_test_set(
    name = "resource_limits",
    test_src = "resource_test.py",
    variants = {
        "production": {
            "assertion": "Production limits correct",
            "env": {"TIER": "prod", "MAX_CONN": "100"},
            "args": ["--strict"],
        },
        "staging": {
            "assertion": "Staging limits correct",
            "env": {"TIER": "staging", "MAX_CONN": "50"},
        },
    },
    assertion = "All deployment tiers validated",
    depends_on = [":base_config_wrapped"],
    env = {"COMMON_VAR": "value"},
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | Base name |
| `test_src` | string | yes | Source file shared across variants |
| `variants` | dict | yes | `{name: {"assertion": ..., "env": {...}, "args": [...], "depends_on": [...]}}` |
| `assertion` | string | no | Overall test set assertion |
| `requirement_id` | string | no | Traceability ID |
| `depends_on` | label_list | no | Dependencies for all variants |
| `env` | dict | no | Default environment variables for all variants |

## CLI: Orchestrator

```bash
bazel run //orchestrator:main -- [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--manifest` | path | required | Path to the JSON manifest file |
| `--mode` | string | `diagnostic` | Execution mode: `diagnostic`, `detection` |
| `--regression` | flag | off | Enable regression selection (narrows test scope to changed-file correlations) |
| `--max-parallel` | int | CPU count | Maximum parallel test executions |
| `--max-failures` | int | unlimited | Stop after N failures |
| `--output` | path | - | Path to write JSON report |
| `--status-file` | path | - | Path to `.tests/status` JSON state file |
| `--diff-base` | string | - | Git ref for regression diff |
| `--changed-files` | string | - | Comma-separated changed files (regression) |
| `--co-occurrence-graph` | path | `.tests/co_occurrence_graph.json` | Path to co-occurrence graph |
| `--max-test-percentage` | float | `0.10` | Max fraction of stable tests for regression |
| `--max-hops` | int | `2` | Max BFS hops in regression expansion |

## CLI: CI Tool

```bash
bazel run //ci_tool:main -- [SUBCOMMAND] [OPTIONS]
```

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `burn-in` | Run burn-in tests with SPRT evaluation |
| `deflake` | Re-run flaky tests to confirm stability |
| `test-status` | Display test maturity status from state file |
| `re-judge` | Re-evaluate judgements with stored measurements |
| `build-graph` | Build/update co-occurrence graph from git history |

## JSON Manifest Format

The manifest generated by `test_set` has this structure:

```json
{
  "test_set": {
    "name": "my_tests",
    "assertion": "All tests pass",
    "requirement_id": "REQ-001",
    "tests": ["//pkg:test_a", "//pkg:test_b"],
    "subsets": ["//pkg:child_set"]
  },
  "test_set_tests": {
    "//pkg:test_a": {
      "assertion": "Feature A works",
      "executable": "path/to/executable",
      "depends_on": [],
      "requirement_id": "REQ-A",
      "judgement_executable": "//pkg:judge_a"
    }
  }
}
```

## JSON Report Format

Reports generated by the orchestrator:

```json
{
  "report": {
    "generated_at": "2026-01-01T00:00:00+00:00",
    "commit": "abc123",
    "summary": {
      "total": 10,
      "passed": 8,
      "failed": 1,
      "dependencies_failed": 1,
      "total_duration_seconds": 12.345
    },
    "test_set": {
      "name": "my_tests",
      "assertion": "All tests pass",
      "status": "failed",
      "tests": {
        "test_a": {
          "assertion": "Feature A works",
          "status": "passed",
          "duration_seconds": 1.234,
          "structured_log": {
            "block_sequence": ["rigging", "stimulation", "verdict"],
            "measurements": []
          }
        }
      }
    }
  }
}
```
