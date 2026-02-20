# Steps within Blocks for Structured Logging - Design Document

## Glossary

| Term | Definition |
|------|------------|
| **Block** | A named phase of test execution (rigging, stimulation, checkpoint, verdict) delimited by `block_start`/`block_end` events. Blocks are the existing top-level structuring unit. |
| **Step** | A named sub-division within a block, delimited by `step_start`/`step_end` events. Steps can nest arbitrarily to form a hierarchy. |
| **Sub-step** | A step that is nested inside another step. A sub-step's parent is the step that was active when the sub-step started. |
| **Innermost step** | The deepest currently-open step in the nesting hierarchy. Logs and events are attributed to this step. |
| **Event** | A structured `[TST]` log line with a JSON payload containing a `type` field. |
| **Attribution** | The process of associating a log line or event with its containing step and block. |
| **Bubbling** | The propagation of certain events (measurement, result, feature, error) from the step where they are emitted up to the containing block. |
| **Undefined step** | A synthetic step with `step == "undefined"` created by the parser to collect the remainder of log lines after a parsing error is encountered within a step hierarchy. Has `status == "warning"`. |
| **Undefined block** | A synthetic block with `block == "undefined"` created by the parser to collect log lines that cannot be attributed to a valid block (e.g., step events outside any block). Has a warning state. |
| **Valid prefix** | The longest prefix of log lines within a span (block or step) that can be parsed without encountering a structural error (mismatched step names, duplicate names, etc.). |

## Overview

This feature adds hierarchical step support within the existing block-based structured logging system. Test authors can subdivide blocks into named steps, and steps can contain sub-steps to arbitrary depth. Steps provide finer-grained structure for understanding test execution, while preserving backward compatibility with the existing block-oriented data model.

## Motivation

Currently, blocks (rigging, stimulation, checkpoint, verdict) provide coarse-grained structure. In practice, a single block -- particularly stimulation -- can contain many logically distinct operations. Without steps, all logs and events within a block are a flat list, making it difficult to:

1. **Identify which operation failed** when a block contains multiple independent actions.
2. **Attribute measurements and results** to specific sub-operations within a block.
3. **Navigate large test output** in HTML reports when blocks contain many events.
4. **Understand failure propagation** -- when one operation fails, which parent operations are affected?

Steps solve these problems by allowing test authors to create a tree of named operations within each block, with clear rules for log attribution, event bubbling, and failure propagation.

## Requirements

### Functional Requirements

1. Test authors can emit `step_start` and `step_end` events within a block to create named steps. Each step has a unique name and a user-friendly description.
2. Steps can nest: a `step_start` inside an already-open step creates a sub-step, to arbitrary depth.
3. Logs (plain text) emitted between the innermost step's start and end are attributed to that step only.
4. Measurement, result, and feature events are stored in the innermost step (original name) AND bubbled to the containing block (step-qualified name).
5. Error events are attributed to the innermost step, and cause the step, all ancestor steps, and the containing block to be marked as failed. The error dict is stored only in the innermost step and the block; parent steps receive status propagation only.
6. The log parser produces a `StepSegment` tree within each `BlockSegment`, representing the step hierarchy.
7. The HTML reporter renders steps as nested, collapsible `<details>` elements. Passed steps are collapsed; failed and warning steps are expanded. The full path from block to the innermost failed step is expanded.
8. Steps must exist within a block -- steps outside blocks produce an undefined block with warning state.
9. `step_end` must specify the step name and must match the innermost open step. Mismatches trigger the valid-prefix-plus-undefined-remainder error recovery.
10. Step names must be unique within their scope (same block or same parent step). Duplicates trigger error recovery.

### Non-Functional Requirements

1. **Backward compatibility**: Existing tests without steps continue to work unchanged. `BlockSegment.steps` defaults to an empty list. Block-level flat lists contain all events as before.
2. **Forward compatibility**: Unknown event types within steps are silently skipped (consistent with existing parser behavior).
3. **Robustness**: Structural errors in step markers produce parser warnings and trigger the valid-prefix-plus-undefined-remainder recovery strategy. The parser never crashes on malformed input.
4. **No new dependencies**: The feature uses only Python standard library facilities already imported by the log parser.

## Architecture

### Components

The following existing components require modification:

| Component | Change |
|-----------|--------|
| **Log Parser** (`orchestrator/analysis/log_parser.py`) | Add `StepSegment` dataclass. Extend both `parse_test_output()` and `parse_stdout_segments()` to handle `step_start`/`step_end` events, build the step tree, and populate `BlockSegment.steps`. |
| **HTML Reporter** (`orchestrator/reporting/html_reporter.py`) | Extend `_render_block_segment()` to render the step tree as nested collapsible sections. Failed steps are expanded by default; passed steps are collapsed. |

No new components are introduced. The feature is contained within the existing log parser and HTML reporter.

### Data Model

#### `StepSegment` (new dataclass)

```python
@dataclass
class StepSegment:
    step: str                                  # unique name
    description: str                           # user-friendly description
    status: str = "passed"                     # "passed" | "failed" | "warning"
    logs: str = ""                             # logs attributed directly to this step
    features: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    steps: list[StepSegment] = field(default_factory=list)   # nested sub-steps
```

#### `BlockSegment` (extended)

```python
@dataclass
class BlockSegment:
    block: str
    description: str = ""
    logs: str = ""
    features: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    steps: list[StepSegment] = field(default_factory=list)   # NEW: top-level steps (tree roots)
```

The block's existing flat lists (`features`, `measurements`, `results`, `errors`, `assertions`) continue to hold the **bubbled-up aggregate** of all events from all steps plus any events emitted directly in the block outside of steps. This preserves backward compatibility: consumers that do not know about steps see the same data they always did.

### Data Flow

Parsing is top-down and hierarchical, proceeding in three phases:

```
Test stdout (lines)
    |
    v
Phase 1: Split into blocks
    |  Scan for block_start/block_end markers.
    |  Each block receives its raw lines (including step markers).
    |  Lines outside blocks -> "untyped" BlockSegment (existing behavior).
    |
    v
list[BlockSegment] (each with raw lines, no steps yet)
    |
    v
Phase 2: Split each block's lines into top-level steps
    |  Within each block, scan for step_start/step_end markers at the
    |  top level. Lines between steps or before the first step remain
    |  in the block's own fields. Each top-level step receives its
    |  raw lines (including sub-step markers).
    |
    v
BlockSegment.steps = [StepSegment, ...] (one level deep, raw lines in each)
    |
    v
Phase 3: Recurse into each step
    |  For each StepSegment, scan its lines for nested step_start/step_end.
    |  Build sub-steps recursively. At each level, parse content events
    |  (measurements, features, results, errors) and apply bubbling rules.
    |  On any structural error: keep the valid prefix, put the remainder
    |  into an "undefined" step with status "warning".
    |
    v
BlockSegment (with fully populated .steps tree)
    |
    +---> ParsedOutput (existing structure, unchanged interface)
    |
    +---> HTML Reporter
              |
              v
          Nested <details> elements for step hierarchy
          (failed steps expanded, warning steps expanded,
           passed steps collapsed)
```

### Interfaces

No new public functions are added. The existing interfaces are extended:

- `parse_test_output()` returns `ParsedOutput` as before; `BlockSegment` objects now have a populated `steps` field when steps are present.
- `parse_stdout_segments()` returns `list[Segment]` as before; `BlockSegment` objects now have a populated `steps` field.
- `_render_block_segment()` (internal to HTML reporter) is extended to render step trees.

## Design Details

### Event Format

Steps use two new event types that parallel the existing `block_start`/`block_end` pattern:

#### `step_start`

```python
tst({"type": "step_start", "step": "<unique_name>", "description": "<user-friendly description>"})
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Always `"step_start"` |
| `step` | Yes | Unique name identifying this step within the test output. Used for matching with the corresponding `step_end`. |
| `description` | Yes | User-friendly text describing what this step does. Intended for readers of test results who may not be familiar with the test code. |

#### `step_end`

```python
tst({"type": "step_end", "step": "<unique_name>"})
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Always `"step_end"` |
| `step` | Yes | Must match the `step` field of the corresponding `step_start`. Used for validation that the correct step is being closed. |

#### Example Usage

```python
tst({"type": "block_start", "block": "stimulation"})

tst({"type": "step_start", "step": "create_order", "description": "Create a new order in the system"})
tst({"type": "measurement", "name": "order_id", "value": 12345})

tst({"type": "step_start", "step": "validate_inventory", "description": "Check that inventory is available for all items"})
tst({"type": "measurement", "name": "items_checked", "value": 3})
tst({"type": "step_end", "step": "validate_inventory"})

tst({"type": "step_start", "step": "process_payment", "description": "Charge the customer's payment method"})
tst({"type": "measurement", "name": "charge_amount", "value": 99.99, "unit": "USD"})
tst({"type": "step_end", "step": "process_payment"})

tst({"type": "step_end", "step": "create_order"})

tst({"type": "block_end", "block": "stimulation"})
```

### Bubbling and Attribution Rules

When a content event is emitted inside a step hierarchy, the parser applies the following rules to determine where the event data is stored:

#### Measurement, Result, and Feature Events

These events are stored in exactly **two** places:

1. **Innermost step**: The event dict is appended to the innermost (deepest open) step's corresponding list (`.measurements`, `.results`/`.assertions`, or `.features`). The event's `name` field retains its **original value** as emitted by the test.
2. **Containing block**: A copy of the event dict is appended to the containing `BlockSegment`'s corresponding list. For measurements, the `name` field is **qualified with the step path** (see below). For features and results, the name is also qualified.

Intermediate parent steps are **skipped** -- they do not receive copies of the event. Consumers that need the full picture of what happened inside a parent step must walk the sub-step tree.

#### Step-Qualified Names

When an event bubbles from a step to the containing block, its `name` field is qualified with the dot-separated path of step names from the block down to the step that emitted it:

```
<step_name>[.<sub_step_name>]*.<original_name>
```

**Example**: Step `"create_order"` contains sub-step `"process_payment"`, which emits `{"type": "measurement", "name": "charge_amount", "value": 99.99}`.

| Location | `name` value |
|----------|-------------|
| `process_payment.measurements` (innermost step) | `"charge_amount"` |
| `BlockSegment.measurements` (bubbled to block) | `"create_order.process_payment.charge_amount"` |

This ensures that the block-level flat lists contain unique, fully-qualified names that encode the step hierarchy. The judgement system and measurements storage receive these qualified names, enabling unambiguous identification of measurements across steps.

Events emitted directly in a block (outside any step) retain their original unqualified names, preserving backward compatibility.

#### Error Events

Error events follow a different propagation pattern:

1. **Innermost step**: The error dict is appended to the innermost step's `.errors` list.
2. **Status propagation**: The innermost step's `.status` is set to `"failed"`. Every ancestor step in the stack also has its `.status` set to `"failed"`. This is status-only propagation -- the error dict is NOT copied into ancestor steps' `.errors` lists.
3. **Containing block**: The error dict is appended to the containing `BlockSegment.errors` list (for backward compatibility with the existing `has_rigging_failure` check and other block-level error consumers).

To find the original error event, consumers walk the step tree looking for steps with non-empty `.errors` lists.

#### Plain Text Lines

Plain text (non-`[TST]`) lines emitted while a step is active are appended to the **innermost step's** `.logs` field only. They are not duplicated to the block's `.logs` or to parent steps.

#### Events Outside Steps (but Inside a Block)

Events emitted inside a block but outside any step follow the existing behavior: they are stored directly in the `BlockSegment`'s flat lists. The step tree is not involved.

#### Summary Table

| Event type | Innermost step | Parent steps | Block |
|------------|---------------|--------------|-------|
| `measurement` | stored in `.measurements` (original name) | -- | stored in `.measurements` (step-qualified name) |
| `result` | stored in `.assertions` (original name) | -- | stored in `.assertions` (step-qualified name) |
| `feature` | stored in `.features` (original name) | -- | stored in `.features` (step-qualified name) |
| `error` | stored in `.errors`, status -> `"failed"` | status -> `"failed"` | stored in `.errors` |
| plain text | appended to `.logs` | -- | -- |

### Consumer Compatibility

#### Inference (`orchestrator/analysis/inference.py`)

No changes required. `get_rigging_features()` reads `BlockSegment.features` from the rigging block. Features declared inside steps within the rigging block bubble up to `BlockSegment.features` (with step-qualified names), so inference continues to work. The qualified names (e.g., `"setup_db.auth_service"`) are acceptable for substring-based matching against test names and assertions.

#### Measurements (`orchestrator/analysis/measurements.py`)

No changes to the measurements module itself. The `store_measurements()` function receives `ParsedOutput.all_measurements`, which aggregates `BlockSegment.measurements` across all blocks. Because measurements bubbled from steps have step-qualified names (e.g., `"create_order.process_payment.charge_amount"`), the stored measurement data automatically includes step context. The judgement system receives unique, fully-qualified names for re-evaluation.

#### Judgement (`orchestrator/analysis/judgement.py`)

No changes required. The Judgement component uses `parse_test_output()` to parse structured output from judgement executables. Since `BlockSegment.steps` defaults to an empty list, judgement executables that do not emit step events produce identical results. If a judgement executable does use steps, the bubbling rules ensure the block-level flat lists contain all events for existing consumers.

#### Reporter (`orchestrator/reporting/reporter.py`)

No changes required. The reporter stores `stdout` in the JSON report. The HTML reporter parses `stdout` at render time via `parse_stdout_segments()`, which will produce the step tree. The JSON report structure is unchanged.

#### Package Exports (`orchestrator/analysis/__init__.py`)

The `StepSegment` dataclass must be added to the `__init__.py` exports so it is accessible to consumers importing from `orchestrator.analysis`.

### HTML Rendering

Steps are rendered as nested collapsible `<details>/<summary>` elements within block cards. The rendering follows these rules:

1. **Collapsed by default**: Passed steps are collapsed so the report remains scannable.
2. **Failed steps expanded**: Any step with `status == "failed"` is rendered with the `open` attribute on its `<details>` element, so the reader immediately sees what went wrong.
3. **Ancestor expansion**: If a sub-step is failed, all of its ancestor steps are also expanded, creating a visible path from the block header down to the failure.
4. **Step header**: Each step's `<summary>` shows a status badge (passed/failed), the step description, and the step name in a muted style.
5. **Step body**: Contains the step's features, measurements table, assertions, errors, and raw logs (collapsed), using the same rendering patterns as blocks.
6. **Nesting**: Sub-steps are rendered inside their parent step's body, indented to show hierarchy.

## Error Handling

### Parsing Strategy: Valid Prefix + Undefined Remainder

The parser uses a consistent error recovery strategy at every level of the hierarchy (blocks and steps). When a structural error is encountered in the open/close markers:

1. **Keep the valid prefix**: All lines up to and including the last successfully parsed element are preserved in their proper structure.
2. **Collect the remainder**: All lines from the point of failure onward are collected into a synthetic "undefined" block or step with `status == "warning"`.
3. **Record a parser warning**: A human-readable warning message is added to `ParsedOutput.warnings`.

This approach maximizes the amount of structured information available to the reader while clearly indicating where parsing broke down.

### Structural Error Cases

#### Case 1: `step_end` name mismatch

The test emits `step_start("A")`, `step_start("B")`, `step_end("A")`. Step `B` is the innermost open step, but the `step_end` names `A`.

**Behavior**: Everything parsed before the mismatched `step_end` is kept as the valid prefix. The mismatched `step_end` line and all subsequent lines within the containing span are collected into an undefined step with `status == "warning"`.

```
step A (valid prefix: contains sub-step B up to the mismatch point)
  +-- step B (valid prefix: content before the bad step_end)
  +-- undefined step (warning): remainder from the bad step_end onward
```

#### Case 2: `step_start` outside any block

A `step_start` event appears before any `block_start` or after a `block_end`.

**Behavior**: The `step_start` and all subsequent lines until the next `block_start` (or end of output) are collected into an undefined block with warning state.

#### Case 3: `block_end` while steps are still open

A `block_end` is encountered but one or more steps were never closed with `step_end`.

**Behavior**: The valid prefix (all properly opened-and-closed steps) is kept. The unclosed step and its contents from the point where structure breaks down are collected into an undefined step with `status == "warning"` within the block. The block itself is still closed.

#### Case 4: Duplicate step names

Two `step_start` events with the same `step` name appear within the same scope (same block or same parent step).

**Behavior**: The first step with that name is parsed normally. When the duplicate `step_start` is encountered, it is treated as a structural error. The valid prefix (including the first step) is kept. The duplicate `step_start` and all subsequent lines are collected into an undefined step with `status == "warning"`.

### Status Values

Steps and blocks can have one of three statuses:

| Status | Meaning | HTML rendering |
|--------|---------|----------------|
| `"passed"` | All operations completed without errors | Collapsed, green badge |
| `"failed"` | An error event was emitted within this span or a child span | Expanded, red/pink badge |
| `"warning"` | A structural parsing error occurred; this span contains unparseable remainder lines | Expanded, yellow/amber badge |

### Interaction Between Error and Warning States

If a step has both a content error (an `error` event) and a structural parse error, `"failed"` takes precedence over `"warning"`. The step is marked as `"failed"`. The parser warning is still recorded in `ParsedOutput.warnings`.

## Testing Strategy

### 1. Log Parser Unit Tests (pytest)

Test file: `orchestrator/analysis/log_parser_test.py` (extend existing test file with new test classes)

| Test case | Description |
|-----------|-------------|
| `test_single_step_in_block` | One step inside a block. Verify step appears in `BlockSegment.steps`, has correct name/description/status, and the block's flat lists contain bubbled events with step-qualified names. |
| `test_nested_steps` | Three levels of nesting (block > step A > step B > step C). Verify the tree structure: A.steps contains B, B.steps contains C. |
| `test_sequential_steps` | Two sibling steps in the same block. Verify both appear as top-level entries in `BlockSegment.steps`. |
| `test_measurement_bubbling` | Measurement emitted in a nested step. Verify: original name in step's `.measurements`, step-qualified name in block's `.measurements`. |
| `test_result_bubbling` | Result emitted in a step. Verify it appears in step's `.assertions` (original name) and block's `.assertions` (qualified name). |
| `test_feature_bubbling` | Feature emitted in a step. Verify it appears in step's `.features` (original name) and block's `.features` (qualified name). |
| `test_error_attribution` | Error emitted in an inner step. Verify: error dict in innermost step's `.errors` only, status `"failed"` on innermost step and all ancestors, error dict in block's `.errors`. |
| `test_plain_text_attribution` | Plain text lines inside a step. Verify they appear in the step's `.logs` only, not in the block's `.logs`. |
| `test_events_outside_steps` | Events inside a block but outside any step. Verify they go directly to block's flat lists (existing behavior). |
| `test_step_end_name_mismatch` | Mismatched `step_end` name. Verify valid prefix is kept, remainder goes to undefined step with `status == "warning"`, parser warning recorded. |
| `test_step_start_outside_block` | `step_start` before any `block_start`. Verify it goes into an undefined block with warning state. |
| `test_block_end_unclosed_steps` | `block_end` while a step is still open. Verify valid prefix kept, unclosed content in undefined step with warning. |
| `test_duplicate_step_names` | Two `step_start` with the same name in the same scope. Verify first is parsed, remainder in undefined step with warning. |
| `test_backward_compat_no_steps` | Block without any steps. Verify output is identical to the current parser (no `steps` entries, flat lists populated as before). |
| `test_step_qualified_name_format` | Deep nesting (3 levels). Verify the qualified name follows the `a.b.c.name` format exactly. |
| `test_failed_takes_precedence_over_warning` | Step with both an error event and a structural parse error. Verify status is `"failed"`, not `"warning"`. |
| `test_parse_stdout_segments_with_steps` | Verify `parse_stdout_segments()` also populates `BlockSegment.steps` correctly (parallel to `parse_test_output()`). |

### 2. HTML Reporter Unit Tests (pytest)

Test file: `tests/test_html_reporter_steps.py` (new test file; HTML reporter does not yet have a dedicated test file)

| Test case | Description |
|-----------|-------------|
| `test_step_renders_as_details` | A block with one passed step. Verify the output contains a `<details>` element (without `open`) for the step. |
| `test_failed_step_expanded` | A block with a failed step. Verify the `<details>` element has the `open` attribute. |
| `test_warning_step_expanded` | A block with a warning (undefined) step. Verify the `<details>` element has the `open` attribute. |
| `test_ancestor_expansion` | A failed step nested 3 levels deep. Verify all ancestor `<details>` elements have `open`. |
| `test_step_header_content` | Verify the `<summary>` contains a status badge, the step description, and the step name. |
| `test_step_body_measurements` | Step with measurements. Verify the measurements table is rendered inside the step's body. |
| `test_step_body_errors` | Step with errors. Verify the error div is rendered inside the step's body. |
| `test_nested_step_indentation` | Nested steps. Verify sub-steps are rendered inside their parent's body (DOM nesting). |
| `test_block_without_steps` | Block with no steps. Verify rendering is unchanged from current behavior. |

### 3. Integration Test (example)

Test file: `examples/ecommerce/order_steps_test.py` (new example test)

A new example test that demonstrates steps within blocks in an end-to-end scenario. The test uses nested steps within a stimulation block to simulate order processing with sub-operations (validate inventory, process payment, send confirmation). The test is included in the existing example test set and produces an HTML report that can be visually inspected.

## Security Considerations

No new security considerations. Step names and descriptions are user-provided strings that flow through the same `html.escape()` sanitization path as existing block names and descriptions in the HTML reporter. No new input vectors are introduced.

## Performance Considerations

1. **Recursive parsing**: The top-down three-phase parsing adds a recursive step for each nesting level. In practice, step nesting depth is expected to be shallow (2-4 levels). The recursion overhead is negligible compared to I/O.

2. **Event dict copying**: When bubbling events to the block, a shallow copy of the event dict is created (to modify the `name` field for qualification without mutating the step-local copy). This is one dict copy per event, which is negligible.

3. **No change to report size**: Steps are parsed from stdout at HTML render time. The JSON report structure is unchanged, so report file sizes are unaffected.

## Dependencies

No new external dependencies. The feature uses only Python standard library facilities (`dataclasses`, `json`) that are already imported by the log parser.

### Internal Dependencies (modified)

| Module | Type of change |
|--------|---------------|
| `orchestrator/analysis/log_parser.py` | Add `StepSegment` dataclass, extend parsing logic in both `parse_test_output()` and `parse_stdout_segments()` |
| `orchestrator/analysis/__init__.py` | Add `StepSegment` to imports and `__all__` exports |
| `orchestrator/reporting/html_reporter.py` | Import `StepSegment`, add `_render_step_segment()` function, extend `_render_block_segment()` to render step trees |

### Internal Dependencies (unchanged but validated)

| Module | Validation |
|--------|-----------|
| `orchestrator/analysis/inference.py` | `get_rigging_features()` works unchanged due to bubbling |
| `orchestrator/analysis/measurements.py` | `store_measurements()` receives step-qualified names via bubbling |
| `orchestrator/analysis/judgement.py` | `parse_test_output()` returns compatible `ParsedOutput`; empty `steps` list when no steps emitted |
| `orchestrator/reporting/reporter.py` | No changes; stdout parsed at HTML render time |

## References

- [Log Parser component](../../current/components/log-parser.md)
- [Reporter component](../../current/components/reporter.md)
- [HTML Reporter component](../../current/components/html-reporter.md)
- [Report Generation flow](../../current/flows/report-generation.md)

## Implementation Priorities

### Priority 1: Log Parser Core -- StepSegment and basic parsing

Add the `StepSegment` dataclass and extend `parse_test_output()` to handle `step_start`/`step_end` events. Implement the three-phase top-down parsing approach. Include bubbling to the block's flat lists (with step-qualified names) and error status propagation. Add the backward compatibility test (`test_backward_compat_no_steps`) first to establish the baseline, then add the core step parsing tests.

**Rationale**: This is the foundation. All other work depends on the parser producing correct step trees.

### Priority 2: Error recovery

Implement the valid-prefix-plus-undefined-remainder recovery strategy for all four structural error cases (name mismatch, outside block, unclosed steps, duplicate names). Add the `"warning"` status and undefined step/block creation. Add the corresponding error recovery tests.

**Rationale**: Error recovery is essential for robustness. Without it, any malformed test output could produce confusing results.

### Priority 3: `parse_stdout_segments()` extension

Extend the segment-based parser to also build step trees within `BlockSegment` objects. This is the parser used by the HTML reporter.

**Rationale**: This must be done before the HTML reporter can render steps. It follows the same patterns as Priority 1, applied to the second parsing function.

### Priority 4: HTML rendering

Extend `_render_block_segment()` to render step trees. Add `_render_step_segment()` with the collapsible `<details>` pattern, status badges, ancestor expansion logic, and warning state rendering. Add HTML reporter tests.

**Rationale**: This delivers the visible value of the feature to test result readers.

### Priority 5: Integration test and package exports

Create the example test (`order_steps_test.py`), update `__init__.py` exports, and run the full test suite to validate end-to-end behavior.

**Rationale**: End-to-end validation confirms all components work together.

## Open Questions

No open questions remain. All design decisions have been resolved.

## Alternatives Considered

### 1. Flat step list with parent references (rejected)

Instead of a tree of `StepSegment` objects, store all steps in a flat list on `BlockSegment`, each with a `parent` field referencing its parent step name. Rejected because:
- Consumers must reconstruct the tree for rendering and traversal.
- The tree structure directly mirrors the nesting, making both parsing and rendering straightforward.

### 2. Bubble events to intermediate parent steps (rejected)

Copy measurement/result/feature events to every ancestor step, not just the innermost step and the block. Rejected because:
- It creates redundant data at every level of the tree.
- Consumers can walk the tree if they need a subtree-aggregate view.
- The two-location model (innermost step + block) is simpler and meets all identified use cases.

### 3. Copy error dicts to parent steps (rejected)

Copy the error dict into every ancestor step's `.errors` list, not just the innermost step and the block. Rejected because:
- It makes it ambiguous where the error actually originated.
- Status propagation (`"failed"`) on ancestors is sufficient to indicate that something below failed.
- Consumers walk the tree to find the root cause, which is clearer than scanning duplicate error entries.

### 4. Single-pass parsing with step stack (rejected)

Process all lines in a single pass, using a stack to track open steps. Rejected in favor of the top-down three-phase approach because:
- Top-down parsing is easier to reason about and maps naturally to the hierarchical structure.
- Error recovery (valid prefix + undefined remainder) is cleaner when each level processes its own span of lines independently.
- The single-pass approach makes it harder to cleanly separate block-level and step-level parsing logic.

### 5. Step events without unique names (rejected)

Allow steps to have only descriptions, with `step_end` closing the innermost open step by position. Rejected because:
- Name-based matching provides validation that the correct step is being closed.
- Unique names enable step-qualified measurement names for the judgement system.
- Named steps are easier to reference in error messages and parser warnings.
