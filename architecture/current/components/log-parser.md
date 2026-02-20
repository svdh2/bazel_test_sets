# Component: Log Parser

## Location

`orchestrator/analysis/log_parser.py`

## Purpose

Parses structured test log events from test stdout. Tests emit machine-readable events prefixed with `[TST] ` followed by JSON. The parser groups events by the block they belong to and categorises blocks into `rigging`, `run_blocks` (stimulation/checkpoint), and `verdict`.

## Interface

### Data Types

```python
@dataclass
class StepSegment:
    step: str               # step name (e.g. "create_order")
    description: str        # human-readable description
    status: str             # "passed", "failed", or "warning"
    logs: str               # plain text lines attributed to this step
    features: list[dict]    # [{"name": ...}]
    measurements: list[dict]  # [{"name": ..., "value": ...}]
    results: list[dict]     # [{"status": ..., "message": ...}]
    errors: list[dict]      # [{"message": ...}]
    assertions: list[dict]  # normalized: [{"description": ..., "status": ...}]
    steps: list[StepSegment]  # nested sub-steps (recursive)

@dataclass
class BlockSegment:
    block: str              # "rigging", "stimulation", "checkpoint", "verdict", "untyped"
    description: str        # from optional block_start description field
    logs: str               # all lines in order (plain text + raw sentinel lines)
    features: list[dict]    # [{"name": ..., "block": ...}]
    measurements: list[dict]  # [{"name": ..., "value": ..., "block": ...}]
    results: list[dict]     # [{"status": ..., "message": ..., "block": ...}]
    errors: list[dict]      # [{"message": ..., "block": ...}]
    assertions: list[dict]  # normalized for display: [{"description": ..., "status": ...}]
    steps: list[StepSegment]  # step tree (empty if no step events in block)

@dataclass
class ParsedOutput:
    rigging: BlockSegment | None    # the single rigging block
    run_blocks: list[BlockSegment]  # stimulation, checkpoint, and untyped blocks in order
    verdict: BlockSegment | None    # the single verdict block
    warnings: list[str]             # parser-level warnings for malformed lines
```

`ParsedOutput` properties: `has_rigging_failure`, `block_sequence`, `all_blocks`, `all_features`, `all_measurements`, `all_results`, `all_errors`.

### parse_test_output

```python
def parse_test_output(lines: list[str] | str) -> ParsedOutput
```

Builds `BlockSegment` objects as it encounters `block_start`/`block_end` events. Text outside typed blocks becomes `BlockSegment(block="untyped")`. Categorises the first `rigging` block into `rigging`, the last `verdict` block into `verdict`, and everything else into `run_blocks`.

### Event Types

| Type | Fields | Description |
|------|--------|-------------|
| `block_start` / `phase` | `block`, `description` (opt) | Opens a named block; sets current block context |
| `block_end` | `block` | Closes a block; resets current block to None |
| `step_start` | `step`, `description` | Opens a named step within a block; supports nesting |
| `step_end` | `step` | Closes the innermost open step |
| `feature` | `name`, `action` (opt) | Declares a feature exercised in the current block |
| `measurement` | `name`, `value`, `unit` (opt) | Records a numeric measurement |
| `result` | `name`/`passed` or `status`/`message` | Records a test result within a block |
| `error` | `message` | Records an error within a block |

All event types also support optional `_file` and `_line` metadata fields indicating the source location that emitted the event. These are preserved through parsing into the structured dicts (features, measurements, results, errors) via the `_copy_source()` helper.

### Helper Functions

```python
def is_rigging_failure(parsed: ParsedOutput) -> bool
def get_rigging_features(parsed: ParsedOutput) -> list[str]
```

### parse_stdout_segments (segment-based parser)

```python
def parse_stdout_segments(stdout: str) -> list[TextSegment | BlockSegment]
```

Parses raw stdout into a sequence of interleaved `TextSegment` (plain text) and `BlockSegment` (structured blocks) for unified rendering. Used by the HTML reporter to detect and render structured logging inline with plain text.

Result events are normalized into `assertions`: `name`/`passed` → `{"description": name, "status": "passed"/"failed"}`; `status`/`message` → `{"description": message, "status": status}`.

### Step Parsing

Both `parse_test_output` and `parse_stdout_segments` handle `step_start`/`step_end` events within blocks via the shared `_parse_steps_in_block()` post-processor. Steps subdivide blocks into named sub-operations with arbitrary nesting depth.

**Behavior**:
- Content events (features, measurements, results, errors) inside a step are stored in the innermost step with original names AND bubbled to the containing block with step-qualified names (e.g. `"step_a.step_b.measurement_name"`)
- Error events set the step's `status` to `"failed"` and propagate that status to all ancestor steps
- Plain text lines are attributed to the innermost step only (not duplicated to block logs)
- Events outside steps but inside a block use existing behavior (stored in block flat lists with original names)

**Error recovery** (valid-prefix-plus-undefined-remainder strategy):
1. `step_end` name mismatch: keeps valid prefix, creates undefined step with `status="warning"` for remainder
2. `step_start` outside any block: creates an undefined block to contain the orphan step
3. `block_end` with unclosed steps: marks unclosed steps with `status="warning"`
4. Duplicate step names in same scope: keeps first occurrence, collects duplicate into undefined step
5. Precedence: `"failed"` > `"warning"` when a step has both an error event and a structural issue

## Dependencies

- Standard library: `json`

## Dependents

- **Inference** (`orchestrator.analysis.inference`): Uses `get_rigging_features` to find features for dependency inference
- **Judgement** (`orchestrator.analysis.judgement`): Parses structured output from judgement executables via `parse_test_output`; stores `ParsedOutput` as `JudgementResult.judgement_output`
- **HTML Reporter** (`orchestrator.reporting.html_reporter`): Uses `parse_stdout_segments` to render structured stdout

## Key Design Decisions

1. **Block-oriented return type**: `parse_test_output` returns a `ParsedOutput` that groups events by the block they belong to (rigging/run_blocks/verdict) rather than by event type. Aggregate accessors (`all_features`, `all_measurements`, etc.) provide flat views across all blocks.

2. **Sentinel prefix `[TST] `**: A fixed prefix makes it easy to distinguish structured events from regular test output. Tests can intermix structured events with normal print statements.

3. **Forward compatibility**: Unknown event types are silently skipped rather than causing errors. This allows new event types to be added without breaking older parsers.

4. **Block scoping**: Events are automatically associated with the current block (set by `block_start`, cleared by `block_end`). This enables block-level analysis (e.g., "did rigging fail?") without requiring each event to repeat the block name.

5. **Untyped blocks**: Text before, between, or after typed blocks is captured in `BlockSegment(block="untyped")` segments so no output is lost.

6. **Malformed line tolerance**: Lines with `[TST]` prefix but invalid JSON are recorded as warnings and skipped, preventing a single malformed log line from crashing the parser.
