# Component: test_set_test Rule

## Location

`rules/test_set_test.bzl`

## Purpose

Wraps an existing Bazel test target (py_test, cc_test, etc.) with DAG metadata. This is the leaf-level building block of the test set system. Each test_set_test declares an assertion about what it verifies, lists its dependencies on other test_set_test targets, and optionally references a separate judgement target.

## Interface

### Rule Attributes

| Attribute | Type | Required | Description |
|-----------|------|----------|-------------|
| `test` | `label` | Yes | The wrapped test target (must be executable) |
| `depends_on` | `label_list` | No | Other test_set_test targets this test depends on (DAG edges) |
| `assertion` | `string` | Yes | Free-form description of what this test verifies |
| `requirement_id` | `string` | No | Optional traceability ID |
| `judgement` | `label` | No | Optional separate judgement executable for re-evaluation |

### Providers

Produces `TestSetTestInfo` provider with fields:
- `test_label`: Label of the wrapped test target
- `assertion`: Assertion string
- `requirement_id`: Requirement ID string
- `depends_on`: List of dependency labels
- `judgement_label`: Optional judgement target label
- `executable`: The generated runner shell script

### Generated Artifacts

- `{name}_runner.sh`: A shell script that invokes the wrapped test's executable, baking in any `args` so the orchestrator can invoke it without knowledge of Bazel's args mechanism.

## Dependencies

- None (this is the foundational rule)

## Dependents

- **test_set rule**: Consumes `TestSetTestInfo` provider to collect tests into sets
- **matrix_test_set macro**: Creates test_set_test targets for each matrix variant
- **parameterized_test_set macro**: Creates test_set_test targets for each variant
- **Orchestrator**: Executes the runner scripts listed in the manifest

## Key Design Decisions

1. **Runner script baking**: The `args` attribute values are baked into the runner script at build time. This means the orchestrator does not need to know about Bazel's test argument mechanism -- it simply executes the runner.

2. **Assertion validation**: The rule fails at analysis time if the assertion is empty, enforcing documentation discipline.

3. **Test-rule wrapping**: This is declared as `test = True` in the Bazel rule definition, making each test_set_test itself a valid Bazel test target that can be run individually with `bazel test`.
