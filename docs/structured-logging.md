# Structured Logging Guide

The structured logging system allows tests to emit machine-readable events
alongside human-readable output. The orchestrator parses these events to
provide rich reporting, measurement tracking, and rigging failure detection.

## tst_sdk: The Recommended Approach

The `tst_sdk` library provides context managers that emit structured `[TST]`
events automatically, with proper nesting, error handling, and exit code
management. Add it as a Bazel dependency:

```starlark
py_test(
    name = "my_raw_test",
    srcs = ["my_test.py"],
    main = "my_test.py",
    deps = ["@test_sets_bazel_rules//tst_sdk:tst"],
)
```

### test_run

Top-level context manager that creates a `Context` and catches critical
assertion errors:

```python
from tst_sdk import test_run

with test_run() as t:
    # ... blocks, steps, assertions ...
    return t.exit_code()
```

### Blocks

Blocks organize test execution into phases. Each block yields a child
context for reporting within that phase:

```python
with t.block("rigging") as b:
    b.feature("database", "connect")
    b.feature("cache", "flush")

with t.block("stimulation", description="Run query") as b:
    # ... steps, measurements ...

with t.block("verdict") as b:
    b.assert_that("query_performance", True)
```

Standard block names:
- `rigging`: Test fixture setup (connecting services, initializing state)
- `stimulation`: Applying the test stimulus
- `checkpoint`: Intermediate verification points
- `verdict`: Final pass/fail determination

### Steps

Steps subdivide blocks into named sub-operations. Steps can nest to
arbitrary depth, and each step yields its own child context:

```python
with t.block("stimulation", description="Place order") as b:
    with b.step("validate_inventory", description="Check stock levels") as s:
        s.measure("stock_check_ms", 15, "ms")
        s.assert_that("all_items_in_stock", True, critical=True)

    with b.step("process_payment", description="Charge payment method") as s:
        s.measure("charge_amount", 129.97, "USD")
        s.assert_that("payment_authorized", True, critical=True)

        # Nested sub-step
        with s.step("send_receipt", description="Email receipt") as s2:
            s2.measure("email_latency_ms", 50, "ms")
            s2.assert_that("receipt_sent", True)
```

### Context Methods

| Method | Description |
|--------|-------------|
| `ctx.block(block_type, **extra)` | Context manager for a named block phase |
| `ctx.step(step_name, **extra)` | Context manager for a named step within a block |
| `ctx.feature(name, action, **extra)` | Declare a feature/service the test exercises |
| `ctx.measure(name, value, unit, **extra)` | Record a quantitative measurement |
| `ctx.assert_that(name, passed, critical=False, **extra)` | Record a pass/fail assertion |
| `ctx.error(name, message, **extra)` | Record an error (raises `CriticalAssertionError`) |
| `ctx.exit_code()` | Returns `1` if any failures, `0` otherwise |

The `critical=True` flag on `assert_that` stops test execution immediately
on failure (skipping subsequent steps), similar to how `error()` works.
Non-critical assertions record the failure but allow the test to continue.

### Error Handling

Exceptions raised inside a `block` or `step` context manager are
automatically caught and recorded as errors. The block/step is closed
cleanly and failures propagate to the parent context:

```python
with t.block("stimulation") as b:
    with b.step("connect", description="Connect to service") as s:
        raise ConnectionError("timeout")  # automatically logged as error
    # execution continues here after the failed step
```

## Complete Example

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

## Wire Protocol Reference

Under the hood, the SDK emits `[TST]`-prefixed JSON events to stdout.
This section documents the raw event format for tool authors and custom
integrations.

### Sentinel Prefix

All structured log events use the `[TST]` sentinel prefix:

```
[TST] {"type": "block_start", "block": "rigging"}
```

Lines without `[TST]` are treated as normal stdout and passed through
unchanged. This allows tests to mix structured events with human-readable
output.

### Event Types

**Block events** -- `block_start` / `block_end`:
```json
{"type": "block_start", "block": "rigging"}
{"type": "block_end", "block": "rigging"}
```

**Step events** -- `step_start` / `step_end`:
```json
{"type": "step_start", "step": "validate_inventory", "description": "Check inventory for all items"}
{"type": "step_end", "step": "validate_inventory"}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | `"step_start"` or `"step_end"` |
| `step` | string | yes | Unique step name (within the block) |
| `description` | string | yes (start only) | User-friendly description of the step |

**Rules:**
- Steps must be inside a block.
- Steps can nest to arbitrary depth.
- `step_end` must name the innermost open step.
- Logs between step start/end are attributed to that step.
- Measurement, result, and feature events bubble to the containing block
  with step-qualified names (e.g., `outer.inner.latency`).
- Errors mark the current step and all ancestor steps as failed.
- In HTML reports, passed steps are collapsed; failed steps are expanded.

**Measurement events**:
```json
{"type": "measurement", "name": "response_time_ms", "value": 42, "unit": "ms"}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"measurement"` |
| `name` | string | yes | Measurement identifier |
| `value` | number/string | yes | Measured value |
| `unit` | string | yes | Unit of measurement |

**Result events**:
```json
{"type": "result", "name": "database_connected", "passed": true}
```

**Feature events**:
```json
{"type": "feature", "name": "payment_gateway", "action": "connect"}
```

**Error events**:
```json
{"type": "error", "name": "ConnectionError", "message": "Connection timeout to payment gateway"}
```

## Graceful Degradation

Tests that do not emit `[TST]` events work normally. The orchestrator
treats them as opaque executables with pass/fail status based on exit code.
Structured logging is optional and additive.

## Report Integration

When structured logs are present, the JSON report includes:

```json
{
  "tests": {
    "my_test": {
      "status": "passed",
      "duration_seconds": 1.234,
      "structured_log": {
        "block_sequence": ["rigging", "stimulation", "checkpoint", "verdict"],
        "measurements": [
          {"name": "query_time_ms", "value": 12, "unit": "ms"}
        ],
        "results": [
          {"name": "data_integrity", "passed": true}
        ],
        "errors": [],
        "has_rigging_failure": false
      }
    }
  }
}
```

## Rigging Failure Detection

If an error event occurs within a `rigging` block, the `has_rigging_failure`
flag is set to `true`. This allows distinguishing between test failures due
to the system under test versus test infrastructure failures.

## Judgement Targets

For tests with separate judgement evaluation, measurements are stored
and can be re-evaluated later without re-running the test:

```starlark
test_set_test(
    name = "perf_wrapped",
    test = ":perf_raw_test",
    assertion = "Performance meets SLA",
    judgement = ":perf_judgement",
)
```

Re-judge with stored measurements:

```bash
bazel run //orchestrator:main -- re-judge \
    --manifest manifest.json \
    --measurements-dir .tests/measurements/
```
