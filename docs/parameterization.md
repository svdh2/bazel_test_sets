# Parameterization Guide

The parameterization macros generate multiple test targets from a single
test source file with different parameters. All parameterization is
resolved at build time -- the orchestrator and rules are completely
unaware of parameters.

## matrix_test_set

Use `matrix_test_set` when you have a grid of parameter combinations and
want to test each one:

```starlark
load("//macros:matrix_test_set.bzl", "matrix_test_set")

matrix_test_set(
    name = "payment_regions",
    test_src = "payment_region_test.py",
    assertion_template = "Payment works in {region} with {currency}",
    matrix = {
        "us": {"region": "US", "currency": "USD"},
        "eu": {"region": "EU", "currency": "EUR"},
        "uk": {"region": "UK", "currency": "GBP"},
        "jp": {"region": "JP", "currency": "JPY"},
    },
    assertion = "Payment processing works across all regions",
    requirement_id = "PAY-REGION",
)
```

### How It Works

For each entry in the matrix, the macro generates:

1. A `py_test` target: `payment_regions_us_test`
2. A `test_set_test` wrapper: `payment_regions_us_wrapped`
3. The test set: `payment_regions` (or `payment_regions_test`)

Parameters are passed as command-line arguments: `--region=US --currency=USD`

### Test Source

Your test source reads parameters via `argparse`:

```python
import argparse
import sys

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--currency", required=True)
    args = parser.parse_args()

    print(f"Testing payment in {args.region} with {args.currency}")
    # ... test logic ...
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### Assertion Template

The `assertion_template` uses Python format string syntax with keys
matching the matrix parameter names:

```python
# This template:
assertion_template = "Payment works in {region} with {currency}"

# With matrix entry {"region": "US", "currency": "USD"} produces:
# "Payment works in US with USD"
```

## parameterized_test_set

Use `parameterized_test_set` when variants have different configurations
(environment variables, arguments, dependencies) and individual assertions:

```starlark
load("//macros:parameterized_test_set.bzl", "parameterized_test_set")

parameterized_test_set(
    name = "resource_limits",
    test_src = "resource_limits_test.py",
    variants = {
        "production": {
            "assertion": "Production tier enforces strict limits",
            "env": {"DEPLOY_TIER": "production", "MAX_CONN": "100"},
            "args": ["--strict"],
        },
        "staging": {
            "assertion": "Staging tier uses relaxed limits",
            "env": {"DEPLOY_TIER": "staging", "MAX_CONN": "50"},
        },
        "development": {
            "assertion": "Development tier allows unlimited access",
            "env": {"DEPLOY_TIER": "development", "MAX_CONN": "10"},
        },
    },
    assertion = "Resource limits correct for all deployment tiers",
    depends_on = [":config_base_wrapped"],
    env = {"COMMON_FLAG": "true"},
)
```

### Variant Configuration

Each variant in the dict can specify:

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `assertion` | string | yes | Assertion for this specific variant |
| `env` | dict | no | Environment variables (merged with shared `env`) |
| `args` | list | no | Command-line arguments for `py_test` |
| `depends_on` | list | no | Additional dependencies for this variant |

### Test Source

Your test source reads configuration from environment variables:

```python
import os
import sys

def main() -> int:
    tier = os.environ.get("DEPLOY_TIER", "development")
    max_conn = int(os.environ.get("MAX_CONN", "10"))
    print(f"Checking {tier} tier with max {max_conn} connections")
    # ... test logic ...
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## Choosing Between Macros

| Feature | matrix_test_set | parameterized_test_set |
|---------|-----------------|----------------------|
| Parameter passing | CLI args (`--key=val`) | Environment variables |
| Assertion | Template (shared format) | Individual per variant |
| Dependencies | Same for all | Can vary per variant |
| Best for | Uniform grid testing | Heterogeneous configurations |

## Generated Targets

Both macros generate targets with predictable names:

```
# matrix_test_set(name="payment_regions", matrix={"us": ..., "eu": ...})
# Generates:
#   payment_regions_us_test       (py_test)
#   payment_regions_us_wrapped    (test_set_test)
#   payment_regions_eu_test       (py_test)
#   payment_regions_eu_wrapped    (test_set_test)
#   payment_regions               (test_set, or payment_regions_test)

# parameterized_test_set(name="limits", variants={"prod": ..., "dev": ...})
# Generates:
#   limits_prod_test              (py_test)
#   limits_prod_wrapped           (test_set_test)
#   limits_dev_test               (py_test)
#   limits_dev_wrapped            (test_set_test)
#   limits                        (test_set, or limits_test)
```

## Composing with Hierarchical Test Sets

Parameterized test sets can be used as subsets in a parent test set:

```starlark
test_set(
    name = "deployment_validation",
    subsets = [
        ":resource_limits",
        ":payment_regions",
    ],
    assertion = "Full deployment validation passes",
)
```
