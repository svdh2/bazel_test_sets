# Structured Logging Guide

The structured logging system allows tests to emit machine-readable events
alongside human-readable output. The orchestrator parses these events to
provide rich reporting, measurement tracking, and rigging failure detection.

## Sentinel Prefix

All structured log events use the `[TST]` sentinel prefix:

```
[TST] {"type": "block_start", "block": "rigging"}
```

Lines without `[TST]` are treated as normal stdout and passed through
unchanged. This allows tests to mix structured events with human-readable
output.

## Event Types

### Block Events

Blocks organize test execution into phases:

```python
# Start a block
tst({"type": "block_start", "block": "rigging"})

# End a block
tst({"type": "block_end", "block": "rigging"})
```

Standard block names:
- `rigging`: Test fixture setup (connecting services, initializing state)
- `stimulation`: Applying the test stimulus
- `checkpoint`: Intermediate verification points
- `verdict`: Final pass/fail determination

### Measurement Events

Record quantitative measurements during test execution:

```python
tst({
    "type": "measurement",
    "name": "response_time_ms",
    "value": 42,
    "unit": "ms"
})
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Must be `"measurement"` |
| `name` | string | yes | Measurement identifier |
| `value` | number/string | yes | Measured value |
| `unit` | string | yes | Unit of measurement |

### Result Events

Record individual pass/fail results within a test:

```python
tst({
    "type": "result",
    "name": "database_connected",
    "passed": True
})
```

### Feature Events

Declare which features/services the test exercises (used for rigging
dependency inference):

```python
tst({
    "type": "feature",
    "name": "payment_gateway",
    "action": "connect"
})
```

### Error Events

Record errors encountered during execution:

```python
tst({
    "type": "error",
    "block": "rigging",
    "message": "Connection timeout to payment gateway"
})
```

## Complete Example

```python
import json
import sys

def tst(event: dict) -> None:
    print(f"[TST] {json.dumps(event)}")

def main() -> int:
    # Rigging phase: set up test fixtures
    tst({"type": "block_start", "block": "rigging"})
    tst({"type": "feature", "name": "database", "action": "connect"})
    tst({"type": "feature", "name": "cache", "action": "flush"})
    tst({"type": "block_end", "block": "rigging"})

    # Stimulation phase: apply test stimulus
    tst({"type": "block_start", "block": "stimulation"})
    tst({"type": "measurement", "name": "query_time_ms", "value": 12, "unit": "ms"})
    tst({"type": "measurement", "name": "rows_returned", "value": 42, "unit": "rows"})
    tst({"type": "block_end", "block": "stimulation"})

    # Checkpoint: intermediate verification
    tst({"type": "block_start", "block": "checkpoint"})
    tst({"type": "result", "name": "data_integrity", "passed": True})
    tst({"type": "block_end", "block": "checkpoint"})

    # Verdict: final determination
    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "query_performance", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0

if __name__ == "__main__":
    sys.exit(main())
```

## Graceful Degradation

Tests that do not emit `[TST]` events work normally. The orchestrator
treats them as opaque executables with pass/fail status based on exit code.
Structured logging is optional and additive.

## Report Integration

When structured logs are present, the YAML report includes:

```yaml
tests:
  my_test:
    status: passed
    duration_seconds: 1.234
    structured_log:
      block_sequence: [rigging, stimulation, checkpoint, verdict]
      measurements:
        - name: query_time_ms
          value: 12
          unit: ms
      results:
        - name: data_integrity
          passed: true
      errors: []
      has_rigging_failure: false
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
bazel run //ci_tool:main -- re-judge \
    --manifest manifest.json \
    --measurements-dir .tests/measurements/
```
