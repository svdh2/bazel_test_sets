# Getting Started Tutorial

This tutorial walks through creating your first test set, from a single test
to a hierarchical DAG of test sets with dependencies.

## Prerequisites

- Bazel 9.x with Bzlmod enabled

## Step 1: Create a Simple Test

Start with a standard `py_test` target. Create `tests/health_check_test.py`:

```python
"""Simple health check test."""
import sys

def main() -> int:
    print("Service health check: OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

Add it to your `BUILD.bazel`:

```starlark
py_test(
    name = "health_check_raw_test",
    srcs = ["health_check_test.py"],
    main = "health_check_test.py",
)
```

## Step 2: Wrap with test_set_test

The `test_set_test` rule wraps an existing test target and adds metadata
for the DAG orchestrator. Load the rule and wrap your test:

```starlark
load("//rules:test_set_test.bzl", "test_set_test")

test_set_test(
    name = "health_check_wrapped",
    test = ":health_check_raw_test",
    assertion = "Service responds to health checks",
    requirement_id = "SVC-001",
)
```

Key attributes:
- `test`: Label of the underlying test target (required)
- `assertion`: Human-readable description of what this test verifies (required)
- `requirement_id`: Optional traceability ID linking to a requirements document
- `depends_on`: List of other `test_set_test` targets this test depends on

## Step 3: Create a Test Set

Group your test_set_test targets into a `test_set`:

```starlark
load("//rules:test_set.bzl", "test_set")

test_set(
    name = "service_tests",
    tests = [":health_check_wrapped"],
    assertion = "Service passes all health and readiness checks",
)
```

## Step 4: Build and Run

Build and test with Bazel:

```bash
# Build all targets
bazel build //tests/...

# Run the test set (generates manifest JSON)
bazel test //tests:service_tests
```

The test_set rule generates a JSON manifest file containing the full DAG
structure, which the orchestrator uses for execution.

## Step 5: Add Dependencies

Create a second test that depends on the health check:

```python
# tests/api_endpoint_test.py
"""API endpoint test -- depends on service being healthy."""
import sys

def main() -> int:
    print("GET /api/v1/users -> 200 OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

Add it with a dependency edge:

```starlark
py_test(
    name = "api_endpoint_raw_test",
    srcs = ["api_endpoint_test.py"],
    main = "api_endpoint_test.py",
)

test_set_test(
    name = "api_endpoint_wrapped",
    test = ":api_endpoint_raw_test",
    assertion = "API endpoint returns correct responses",
    depends_on = [":health_check_wrapped"],
)
```

Update your test_set:

```starlark
test_set(
    name = "service_tests",
    tests = [
        ":health_check_wrapped",
        ":api_endpoint_wrapped",
    ],
    assertion = "Service passes all health and API checks",
)
```

The orchestrator will run `health_check_wrapped` before `api_endpoint_wrapped`
because of the `depends_on` edge. If the health check fails, the API test
is automatically marked as `dependencies_failed`.

## Step 6: Run with the Orchestrator

Use the orchestrator to execute with DAG ordering:

```bash
# Diagnostic mode (all tests, leaves-first order)
bazel run //orchestrator:main -- \
    --manifest bazel-bin/tests/service_tests_manifest.json \
    --mode diagnostic

# Detection mode (fast failure, roots-first order)
bazel run //orchestrator:main -- \
    --manifest bazel-bin/tests/service_tests_manifest.json \
    --mode detection
```

## Step 7: Add Structured Logging

Enhance your test to emit structured log events:

```python
"""API endpoint test with structured logging."""
import json
import sys

def tst(event: dict) -> None:
    print(f"[TST] {json.dumps(event)}")

def main() -> int:
    tst({"type": "block_start", "block": "rigging"})
    tst({"type": "feature", "name": "api_server", "action": "connect"})
    tst({"type": "block_end", "block": "rigging"})

    tst({"type": "block_start", "block": "stimulation"})
    tst({"type": "measurement", "name": "response_time_ms", "value": 42, "unit": "ms"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "api_response_ok", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0

if __name__ == "__main__":
    sys.exit(main())
```

The orchestrator parses these events and includes them in the JSON report.

## Step 8: Hierarchical Nesting

Create parent test sets that include child test sets as subsets:

```starlark
test_set(
    name = "api_tests",
    tests = [":api_endpoint_wrapped"],
    assertion = "All API endpoints respond correctly",
)

test_set(
    name = "full_service_tests",
    subsets = [
        ":api_tests",
        ":health_tests",
    ],
    assertion = "Full service validation passes",
)
```

## Next Steps

- See [API Reference](api-reference.md) for all rule attributes
- See [Execution Modes](execution-modes.md) for diagnostic vs detection modes and the regression flag
- See [Structured Logging](structured-logging.md) for the event schema
- See [Burn-in](burn-in.md) for the test maturity lifecycle
- See [Parameterization](parameterization.md) for matrix test sets
- See [Regression](regression-mode.md) for co-occurrence-based test selection
