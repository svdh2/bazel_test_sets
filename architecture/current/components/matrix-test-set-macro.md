# Component: matrix_test_set Macro

## Location

`macros/matrix_test_set.bzl`

## Purpose

Generates a test set from a matrix of parameter combinations. Each entry in the matrix dict produces a concrete py_test target, a test_set_test wrapper, and all are collected into a single test_set. Parameters are baked as command-line arguments (`--key=value`) at build time.

## Interface

```starlark
matrix_test_set(
    name,                   # Base name for generated targets
    test_src,               # Source file shared across all combinations
    assertion_template,     # Python format string with {param} placeholders
    matrix,                 # Dict of {variant_name: {param: value, ...}}
    **kwargs,               # Passed through to test_set
)
```

### Generated Targets (per variant)

For a variant named `us` in a matrix_test_set named `payment_region`:
- `payment_region_us_test` (py_test)
- `payment_region_us_wrapped` (test_set_test)
- `payment_region` (test_set collecting all wrapped targets)

## Dependencies

- **rules_python `py_test`**: Creates the underlying test targets
- **test_set_test rule**: Wraps each py_test with DAG metadata
- **test_set rule**: Collects all wrapped variants into a single set

## Dependents

- User BUILD files that need cross-product parameterized tests
- Other test_set targets that include matrix-generated sets as subsets

## Key Design Decisions

1. **Build-time parameterization**: All parameter resolution happens in Starlark during the loading phase. The orchestrator and rules are completely unaware of parameters.

2. **Assertion templating**: The `assertion_template` uses Python format string syntax, filled with matrix values (`{region}`, `{currency}`), producing human-readable assertions per variant.

3. **No dependency support between variants**: Variants are independent leaf tests with no depends_on edges between them. For variant-specific dependencies, use parameterized_test_set instead.
