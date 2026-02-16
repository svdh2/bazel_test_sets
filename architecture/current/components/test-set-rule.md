# Component: test_set Rule

## Location

`rules/test_set.bzl`

## Purpose

Groups test_set_test targets and child test_set subsets into a hierarchical test set. This is the organizational unit that defines the DAG structure. At build time, it collects all tests transitively from direct tests and nested subsets, produces a JSON manifest describing the full DAG, and generates a runner script that invokes the orchestrator with that manifest.

## Interface

### Macro API (public)

```starlark
test_set(
    name,
    tests = [],       # List of test_set_test labels
    subsets = [],      # List of child test_set labels
    assertion = "",    # Required non-empty assertion
    requirement_id = "",
    **kwargs,
)
```

The macro handles Bazel's naming convention (test rule names must end with `_test`) automatically. If the name does not end with `_test` or `_tests`, it creates the rule target with `_test` appended and an alias with the original name.

### Providers

Produces `TestSetInfo` provider with fields:
- `name`: Name of the test set
- `assertion`: Assertion string
- `requirement_id`: Optional traceability ID
- `test_labels`: Direct test_set_test labels
- `subset_labels`: Direct child test_set labels
- `all_tests`: Depset of all TestSetTestInfo across the entire tree

### Generated Artifacts

- `{name}_manifest.json`: JSON manifest with complete DAG structure
- `{name}_runner.sh`: Shell script that invokes the orchestrator with the manifest

### Manifest JSON Schema

```json
{
  "test_set": {
    "name": "...",
    "assertion": "...",
    "requirement_id": "...",
    "tests": ["//label:a", ...],
    "subsets": ["//label:child_set", ...]
  },
  "test_set_tests": {
    "//label:a": {
      "assertion": "...",
      "executable": "path/to/runner.sh",
      "depends_on": ["//label:b"],
      "requirement_id": "...",
      "judgement_executable": "path/to/judge"
    }
  }
}
```

## Dependencies

- **test_set_test rule**: Provides `TestSetTestInfo` for each test
- **Orchestrator binary** (`//orchestrator:main`): Referenced as a private `_orchestrator` attribute, bundled into runfiles

## Dependents

- **test_set rule** (self-referential): Subsets are themselves test_set targets, enabling arbitrary nesting
- **Orchestrator**: Consumes the manifest JSON at run time
- **CI Tool**: The re-judge command loads manifests produced by test_set

## Key Design Decisions

1. **Transitive collection via depset**: Tests are collected using Bazel's depset mechanism, ensuring efficient transitive aggregation even in deep hierarchies.

2. **Manifest as the contract**: The JSON manifest is the sole interface between build time (Starlark) and run time (Python). The orchestrator has no Bazel dependency at execution time.

3. **Runner generates output directory**: The runner script creates `target/reports/{name}.yaml` by default, placing reports in a predictable location.

4. **Orchestrator bundled as runfile**: The orchestrator binary is included in runfiles, so `bazel test` or `bazel run` on a test_set target works without additional setup.
