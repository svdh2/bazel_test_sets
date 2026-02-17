# Component: Log Parser

## Location

`orchestrator/analysis/log_parser.py`

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
| `block_start` / `phase` | `block`, `description` (opt) | Opens a named block; sets current block context |
| `block_end` | `block` | Closes a block; resets current block to None |
| `feature` | `name`, `action` (opt) | Declares a feature exercised in the current block |
| `measurement` | `name`, `value`, `unit` (opt) | Records a numeric measurement |
| `result` | `name`/`passed` or `status`/`message` | Records a test result within a block |
| `error` | `message` | Records an error within a block |

### Helper Functions

```python
def is_rigging_failure(parsed: dict) -> bool
def get_rigging_features(parsed: dict) -> list[str]
```

### parse_stdout_segments (segment-based parser)

```python
def parse_stdout_segments(stdout: str) -> list[TextSegment | BlockSegment]
```

Parses raw stdout into a sequence of interleaved `TextSegment` (plain text) and `BlockSegment` (structured blocks) for unified rendering. Used by the HTML reporter to detect and render structured logging inline with plain text.

```python
@dataclass
class TextSegment:
    text: str

@dataclass
class BlockSegment:
    block: str              # "rigging", "stimulation", "checkpoint", "verdict"
    description: str        # from optional block_start description field
    logs: str               # plain text emitted during block
    error: str | None       # error that terminated block
    features: list[dict]    # rigging & stimulation
    measurements: list[dict]  # stimulation
    assertions: list[dict]  # checkpoint/verdict: [{"description": ..., "status": ...}]
```

Block-specific fields:
- **All blocks**: `logs` (unstructured text during execution), `error` (termination error)
- **rigging & stimulation**: `features` (services/features triggered)
- **stimulation**: `description`, `measurements`
- **checkpoint/verdict**: `assertions` (each with `description` and `status`)

Result events are normalized: `name`/`passed` → `{"description": name, "status": "passed"/"failed"}`; `status`/`message` → `{"description": message, "status": status}`.

## Dependencies

- Standard library: `json`

## Dependents

- **Inference** (`orchestrator.analysis.inference`): Uses `get_rigging_features` to find features for dependency inference
- **Judgement** (`orchestrator.analysis.judgement`): Parses structured output from judgement executables via `parse_test_output`
- **HTML Reporter** (`orchestrator.reporting.html_reporter`): Uses `parse_stdout_segments` to render structured stdout

## Key Design Decisions

1. **Sentinel prefix `[TST] `**: A fixed prefix makes it easy to distinguish structured events from regular test output. Tests can intermix structured events with normal print statements.

2. **Forward compatibility**: Unknown event types are silently skipped rather than causing errors. This allows new event types to be added without breaking older parsers.

3. **Block scoping**: Events are automatically associated with the current block (set by `block_start`, cleared by `block_end`). This enables block-level analysis (e.g., "did rigging fail?") without requiring each event to repeat the block name.

4. **Malformed line tolerance**: Lines with `[TST]` prefix but invalid JSON are recorded as warnings and skipped, preventing a single malformed log line from crashing the parser.
