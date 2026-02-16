# Flow: Manifest Generation

## Purpose

Build-time flow that transforms Starlark rule declarations in BUILD files into a JSON manifest that the Python orchestrator can consume at run time. This is the bridge between the Bazel build graph and the test execution engine.

## Trigger

Any `bazel build`, `bazel test`, or `bazel run` command targeting a `test_set` target.

## Steps

### 1. Loading Phase: Starlark Evaluation

Bazel evaluates the BUILD file containing the `test_set` macro call. If parameterization macros are used, they expand first.

#### Macro Expansion (if applicable)

For `matrix_test_set`:
```
matrix_test_set(name="payment_region", matrix={...})
    |
    +--> py_test(name="payment_region_us_test", ...)
    +--> test_set_test(name="payment_region_us_wrapped", ...)
    +--> py_test(name="payment_region_eu_test", ...)
    +--> test_set_test(name="payment_region_eu_wrapped", ...)
    +--> test_set(name="payment_region", tests=[...])
```

**Components**: matrix_test_set macro, parameterized_test_set macro

### 2. Analysis Phase: Provider Propagation

Bazel resolves target dependencies and propagates providers:

```
test_set_test (produces TestSetTestInfo)
    |
    v
test_set (consumes TestSetTestInfo, produces TestSetInfo)
    |
    +---> direct tests: list of TestSetTestInfo
    +---> subsets: list of TestSetInfo (recursive)
    +---> all_tests: depset of all TestSetTestInfo (transitive)
```

**Components**: test_set_test rule, test_set rule

### 3. Action Phase: Manifest JSON Generation

The `_test_set_rule_test_impl` rule implementation:

1. Collects all `TestSetTestInfo` from the transitive `all_tests` depset
2. Converts each to a manifest dict entry:
   ```python
   {
       "assertion": info.assertion,
       "executable": info.executable.short_path,
       "depends_on": [str(d) for d in info.depends_on],
       "requirement_id": info.requirement_id,     # if present
       "judgement_executable": str(info.judgement_label),  # if present
   }
   ```
3. Writes the complete manifest as `{name}_manifest.json`

**Components**: test_set rule

### 4. Action Phase: Runner Script Generation

The rule generates `{name}_runner.sh`:

```bash
#!/bin/bash
# Locate runfiles
R="$RUNFILES_DIR" or "$0.runfiles"

# Create output directory
OUT_DIR="${BUILD_WORKSPACE_DIRECTORY:-.}/target/reports"
mkdir -p "$OUT_DIR"

# Execute orchestrator with manifest
exec "$R/{workspace}/orchestrator/main" \
    --manifest "$R/{workspace}/{name}_manifest.json" \
    --output "$OUT_DIR/{name}.yaml" \
    "$@"
```

The `"$@"` at the end passes through any additional CLI arguments (e.g., `--mode detection`).

**Components**: test_set rule

### 5. Runfiles Assembly

The rule bundles into runfiles:
- The manifest JSON file
- The orchestrator binary and its transitive dependencies
- All wrapped test executables and their runfiles (transitive through tests and subsets)

## Data Flow

```
BUILD.bazel
    |
    v
[Starlark loading phase]
    |
    +---> py_test targets (raw tests)
    +---> test_set_test wrappers (TestSetTestInfo providers)
    +---> test_set groupings (TestSetInfo providers, transitive depsets)
    |
    v
[Bazel analysis phase]
    |
    +---> Provider propagation through the target graph
    |
    v
[Bazel action phase]
    |
    +---> {name}_manifest.json    (JSON manifest)
    +---> {name}_runner.sh        (Shell script entry point)
    +---> runfiles/               (All executables + manifest)
    |
    v
[Run time]
    |
    +---> runner.sh invokes orchestrator/main.py --manifest manifest.json
```

## Example Manifest

For the e-commerce demo with nested subsets:

```json
{
  "test_set": {
    "name": "ecommerce_tests",
    "assertion": "E-commerce platform passes all tests",
    "requirement_id": "ECOM-ROOT",
    "tests": [],
    "subsets": [
      "//ecommerce:checkout_flow_tests",
      "//ecommerce:user_account_tests",
      "//ecommerce:payment_region",
      "//ecommerce:resource_limits"
    ]
  },
  "test_set_tests": {
    "//ecommerce:credit_card_wrapped": {
      "assertion": "Credit card payments are authorized and receipts generated",
      "executable": "ecommerce/credit_card_wrapped_runner.sh",
      "depends_on": [],
      "requirement_id": "PAY-001"
    },
    "//ecommerce:order_placement_wrapped": {
      "assertion": "Full order placement completes with payment and inventory update",
      "executable": "ecommerce/order_placement_wrapped_runner.sh",
      "depends_on": [
        "//ecommerce:cart_wrapped",
        "//ecommerce:inventory_wrapped",
        "//ecommerce:credit_card_wrapped"
      ],
      "requirement_id": "ORD-001"
    }
  }
}
```

## Key Design Decisions

1. **Manifest as the single interface**: The JSON manifest is the only artifact that crosses the Starlark/Python boundary. This decouples the build system rules from the execution engine.

2. **Transitive depset collection**: Using Bazel's depset mechanism ensures efficient collection even with deeply nested subsets, without duplication.

3. **Short paths for executables**: The manifest uses `short_path` for executables, which are resolved relative to the runfiles directory at run time.

4. **Pass-through arguments**: The runner script forwards `"$@"` to the orchestrator, allowing users to override mode, max-failures, and other settings at run time without modifying BUILD files.
