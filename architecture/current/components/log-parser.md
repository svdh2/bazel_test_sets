# Component: Log Parser

## Location

`orchestrator/log_parser.py`

## Purpose

Parses structured test log events from test stdout. Tests emit machine-readable events prefixed with `[TST] ` followed by JSON. The parser extracts block phases, features, measurements, results, and errors while collecting non-sentinel lines as plain output.

## Interface

### parse_test_output

```python
def parse_test_output(lines: list[str] | str) -> dict[str, Any]
```

Returns:
```python
{
    "block_sequence": ["rigging", "execution", ...],   # Phase names in order
    "features": [{"name": "auth", "block": "rigging"}],
    "measurements": [{"name": "latency_ms", "value": 42, "block": "execution"}],
    "results": [{"status": "passed", "message": "...", "block": "execution"}],
    "errors": [{"message": "...", "block": "rigging"}],
    "plain_output": ["non-sentinel lines..."],
    "warnings": ["parser warnings for malformed lines..."],
    "has_rigging_failure": true/false,
}
```

### Event Types

| Type | Fields | Description |
|------|--------|-------------|
| `block_start` / `phase` | `block` | Opens a named block; sets current block context |
| `block_end` | `block` | Closes a block; resets current block to None |
| `feature` | `name` | Declares a feature exercised in the current block |
| `measurement` | `name`, `value` | Records a numeric measurement |
| `result` | `status`, `message` | Records a test result within a block |
| `error` | `message` | Records an error within a block |

### Helper Functions

```python
def is_rigging_failure(parsed: dict) -> bool
def get_rigging_features(parsed: dict) -> list[str]
```

## Dependencies

- Standard library: `json`

## Dependents

- **Inference** (`orchestrator.inference`): Uses `get_rigging_features` to find features for dependency inference
- **Judgement** (`orchestrator.judgement`): Parses structured output from judgement executables
- **Reporter** (`orchestrator.reporter`): Includes structured log data in reports

## Key Design Decisions

1. **Sentinel prefix `[TST] `**: A fixed prefix makes it easy to distinguish structured events from regular test output. Tests can intermix structured events with normal print statements.

2. **Forward compatibility**: Unknown event types are silently skipped rather than causing errors. This allows new event types to be added without breaking older parsers.

3. **Block scoping**: Events are automatically associated with the current block (set by `block_start`, cleared by `block_end`). This enables block-level analysis (e.g., "did rigging fail?") without requiring each event to repeat the block name.

4. **Malformed line tolerance**: Lines with `[TST]` prefix but invalid JSON are recorded as warnings and skipped, preventing a single malformed log line from crashing the parser.
