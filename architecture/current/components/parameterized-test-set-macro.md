# Component: parameterized_test_set Macro

## Location

`macros/parameterized_test_set.bzl`

## Purpose

Generates a test set from named variants, each with its own arguments, environment variables, assertion, and optional dependencies. More expressive than matrix_test_set, supporting per-variant environment configuration and dependency edges.

## Interface

```starlark
parameterized_test_set(
    name,                   # Base name for generated targets
    test_src,               # Source file shared across all variants
    variants,               # Dict of {variant_name: config_dict}
    assertion = "",         # Assertion for the overall test_set
    requirement_id = "",    # Optional requirement ID
    depends_on = [],        # Dependencies shared by all variants
    env = {},               # Default environment variables for all variants
    **kwargs,
)
```

### Variant Config Dict

```python
{
    "assertion": "...",          # Required
    "args": ["--flag"],          # Optional CLI args
    "env": {"KEY": "VALUE"},     # Optional env vars (merged with defaults)
    "depends_on": [":target"],   # Optional per-variant dependencies
}
```

### Generated Targets (per variant)

For a variant named `production` in a parameterized_test_set named `resource_limits`:
- `resource_limits_production_test` (py_test with env vars)
- `resource_limits_production_wrapped` (test_set_test)
- `resource_limits` (test_set collecting all wrapped targets)

## Dependencies

- **rules_python `py_test`**: Creates the underlying test targets
- **test_set_test rule**: Wraps each py_test with DAG metadata
- **test_set rule**: Collects all wrapped variants into a single set

## Dependents

- User BUILD files that need environment-driven parameterized tests
- Other test_set targets that include parameterized sets as subsets

## Key Design Decisions

1. **Environment variable merging**: Default `env` from the macro is merged with variant-specific `env`, with variant values taking precedence.

2. **Per-variant dependencies**: Each variant can declare its own `depends_on` in addition to the shared `depends_on` at the macro level, enabling variant-specific DAG edges.

3. **Auto-generated assertion**: If no top-level assertion is provided, the macro generates "All {name} variants pass" as a default.

4. **Automatic parameter extraction**: The macro parses `--key=value` arguments from each variant's `args` list into a `parameters` dict and passes it to `test_set_test`. This makes CLI arguments visible as structured metadata in the manifest and reports without requiring the user to declare parameters separately. Arguments that do not match the `--key=value` pattern are ignored.
