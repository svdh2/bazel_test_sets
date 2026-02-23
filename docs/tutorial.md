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

The `test_set` target bundles the manifest, orchestrator, and all test
executables together. Run it directly — no need to build the manifest
separately:

```bash
# Diagnostic mode (default — all tests, leaves-first order)
bazel run //tests:service_tests

# Diagnostic mode with explicit flag
bazel run //tests:service_tests -- --mode diagnostic

# Detection mode (fast failure, roots-first order)
bazel run //tests:service_tests -- --mode detection
```

Any extra flags after `--` are forwarded to the orchestrator.

## Step 7: Add Structured Logging

Enhance your test with the `tst_sdk` library to emit structured log events.
First, add the dependency to your `BUILD.bazel`:

```starlark
py_test(
    name = "api_endpoint_raw_test",
    srcs = ["api_endpoint_test.py"],
    main = "api_endpoint_test.py",
    deps = ["@test_sets_bazel_rules//tst_sdk:tst"],
)
```

Then rewrite your test to use the SDK's context managers:

```python
"""API endpoint test with structured logging."""
import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("api_server", "connect")

        # --- Stimulation ---
        with t.block("stimulation", description="Test API endpoint") as b:
            # Steps break down the block into named sub-operations
            with b.step("send_request", description="Send GET /api/v1/users") as s:
                time.sleep(0.01)
                s.measure("response_time_ms", 42, "ms")

            with b.step("check_response", description="Validate response body") as s:
                s.assert_that("status_code_200", True)
                s.assert_that("body_has_users", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("api_response_ok", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
```

The `tst_sdk` handles all event emission, error handling, and exit code
management automatically. The orchestrator parses these events and includes
them in the JSON report. Steps appear as collapsible sections in the HTML
report -- failed steps are expanded automatically so readers can quickly
find what went wrong.

See [Structured Logging](structured-logging.md) for the full SDK reference.

## Step 8: View the HTML Report

The orchestrator writes a JSON report to `target/reports/`. Generate a
self-contained HTML report you can open in a browser:

```python
from pathlib import Path
from orchestrator.reporting.html_reporter import write_html_report
import json

report_data = json.loads(Path("target/reports/service_tests.json").read_text())
write_html_report(report_data, Path("report.html"))
```

The HTML report includes:

- A summary header with pass/fail counts and duration
- An **interactive DAG** showing test sets and tests as graph nodes — click
  any node to open a detail pane with its assertion, structured log blocks,
  measurements, and history timeline
- Color-coded structured log blocks (rigging, stimulation, checkpoint, verdict)
  with inline measurement tables and assertion checklists
- Lifecycle badges (`STABLE`, `BURNING IN`, `FLAKY`) and reliability percentages
  when a status file is configured

See the [Reporting guide](reporting.md) for full details.

## Step 9: Hierarchical Nesting

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
- See [Reporting](reporting.md) for the interactive HTML report and DAG visualization
- See [Structured Logging](structured-logging.md) for the event schema
- See [Burn-in](burn-in.md) for the test maturity lifecycle
- See [Parameterization](parameterization.md) for matrix test sets
- See [Regression](regression-mode.md) for co-occurrence-based test selection
