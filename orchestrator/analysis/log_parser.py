"""Structured test log parser.

Parses [TST] sentinel lines from test stdout, dispatching on the type
field to extract block phases, features, measurements, results, and errors.
Unknown types and malformed lines are skipped for forward compatibility.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Union

# Sentinel prefix for structured log lines
SENTINEL = "[TST] "


@dataclass
class StepSegment:
    """A named step within a block, forming a hierarchy of operations.

    Steps subdivide blocks into named sub-operations with arbitrary
    nesting depth.  Each step tracks its own logs, features, measurements,
    results, errors, and assertions.  Content events (measurements, results,
    features) are stored in the innermost step with their original names
    and also bubbled to the containing ``BlockSegment`` with step-qualified
    names (e.g. ``"step_a.step_b.measurement_name"``).

    Error events set the step's ``status`` to ``"failed"`` and propagate
    that status to all ancestor steps.  Plain text lines are attributed to
    the innermost step only.

    The ``steps`` field allows recursive nesting of sub-steps.
    """

    step: str
    description: str
    status: str = "passed"
    logs: str = ""
    features: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    steps: list[StepSegment] = field(default_factory=list)


@dataclass
class BlockSegment:
    """A structured block delimited by block_start/block_end events.

    Each block groups the features, measurements, results, errors, and
    logs that were emitted while the block was active.

    The ``block`` field holds the block type name (e.g. ``"rigging"``,
    ``"stimulation"``, ``"checkpoint"``, ``"verdict"``).  A special value
    of ``"untyped"`` captures output that falls outside any typed block.

    The ``logs`` field contains **all** lines emitted during the block in
    their original order, including both plain-text output and structured
    ``[TST]`` sentinel lines.  This preserves the raw timeline so that
    interleaving of structured and unstructured output is visible for
    debugging, while the typed fields (features, measurements, …) give
    the parsed / synthetic view.
    """

    block: str
    description: str = ""
    logs: str = ""
    features: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    steps: list[StepSegment] = field(default_factory=list)


@dataclass
class ParsedOutput:
    """Block-oriented parsed test output.

    Groups the parsed log events by the block they belong to rather than
    by event type.  The three top-level fields correspond to the canonical
    test phases:

    * ``rigging`` – the single rigging block (or ``None``).
    * ``run_blocks`` – stimulation, checkpoint, and untyped blocks in order.
    * ``verdict`` – the single verdict block (or ``None``).
    """

    rigging: BlockSegment | None = None
    run_blocks: list[BlockSegment] = field(default_factory=list)
    verdict: BlockSegment | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_rigging_failure(self) -> bool:
        """True if the rigging block contains any errors."""
        if self.rigging is None:
            return False
        return len(self.rigging.errors) > 0

    @property
    def all_blocks(self) -> list[BlockSegment]:
        """All blocks in order: rigging, run_blocks, verdict."""
        blocks: list[BlockSegment] = []
        if self.rigging is not None:
            blocks.append(self.rigging)
        blocks.extend(self.run_blocks)
        if self.verdict is not None:
            blocks.append(self.verdict)
        return blocks

    @property
    def block_sequence(self) -> list[str]:
        """Block names in order (excludes ``"untyped"``)."""
        return [b.block for b in self.all_blocks if b.block != "untyped"]

    @property
    def all_features(self) -> list[dict[str, Any]]:
        """All features across all blocks."""
        return [f for b in self.all_blocks for f in b.features]

    @property
    def all_measurements(self) -> list[dict[str, Any]]:
        """All measurements across all blocks."""
        return [m for b in self.all_blocks for m in b.measurements]

    @property
    def all_results(self) -> list[dict[str, Any]]:
        """All results across all blocks."""
        return [r for b in self.all_blocks for r in b.results]

    @property
    def all_errors(self) -> list[dict[str, Any]]:
        """All errors across all blocks."""
        return [e for b in self.all_blocks for e in b.errors]


def _finalize_block(seg: BlockSegment) -> BlockSegment:
    """Strip trailing whitespace from accumulated logs."""
    seg.logs = seg.logs.strip("\n")
    return seg


def _copy_source(dst: dict[str, Any], src: dict[str, Any]) -> None:
    """Copy ``_file`` and ``_line`` source metadata if present."""
    if "_file" in src:
        dst["_file"] = src["_file"]
    if "_line" in src:
        dst["_line"] = src["_line"]


def _build_step_qualified_name(step_path: list[str], name: str) -> str:
    """Build a step-qualified name from the step path and event name.

    Example: step_path=["create_order", "process_payment"], name="charge_amount"
    returns "create_order.process_payment.charge_amount"
    """
    return ".".join(step_path + [name])


def _parse_steps_in_block(
    block: BlockSegment,
    raw_lines: list[str],
    parser_warnings: list[str],
    *,
    use_assertions: bool = False,
) -> None:
    """Post-process a block's raw lines to extract step structure.

    Scans *raw_lines* for ``step_start``/``step_end`` events and populates
    ``block.steps`` with a tree of ``StepSegment`` objects.  Content events
    inside steps are attributed to the innermost step (original name) and
    bubbled to *block* (step-qualified name).  Plain text inside steps goes
    to the innermost step's ``.logs`` only, not to the block's ``.logs``.

    After this function returns, ``block.logs`` contains only lines that
    are outside any step.  The block's flat lists (features, measurements,
    results, errors) contain the union of events outside steps (original
    names) and events bubbled from steps (step-qualified names).

    This function modifies *block* in place.
    """
    # Quick check: if no step_start events in raw_lines, nothing to do.
    has_steps = False
    for line in raw_lines:
        if line.startswith(SENTINEL):
            try:
                entry = json.loads(line[len(SENTINEL):])
                if isinstance(entry, dict) and entry.get("type") == "step_start":
                    has_steps = True
                    break
            except (json.JSONDecodeError, TypeError):
                pass
    if not has_steps:
        return

    # Clear the block's flat lists and logs -- we will rebuild them from
    # scratch, attributing events to steps or block as appropriate.
    block.logs = ""
    block.features.clear()
    block.measurements.clear()
    block.results.clear()
    block.errors.clear()
    if use_assertions:
        block.assertions.clear()

    # Step stack: list of (StepSegment, step_path) tuples.
    # step_path is the list of step names from the block root to this step.
    step_stack: list[tuple[StepSegment, list[str]]] = []
    # Track step names per scope for duplicate detection.
    # Key: id of the parent container (block or step), Value: set of names.
    scope_names: dict[int, set[str]] = {id(block): set()}

    def _innermost_step() -> tuple[StepSegment, list[str]] | None:
        return step_stack[-1] if step_stack else None

    def _append_to_logs(target_logs: str, line: str) -> str:
        if target_logs:
            return target_logs + "\n" + line
        return line

    def _current_scope_id() -> int:
        """Return the id of the current scope container for name tracking."""
        if step_stack:
            return id(step_stack[-1][0])
        return id(block)

    def _create_undefined_step(
        warning_msg: str,
    ) -> tuple[StepSegment, list[str]]:
        """Create an undefined step with warning status and attach it.

        Adds the step to the current parent (innermost open step or block).
        Records a parser warning.  Returns the (step, step_path) tuple
        for use as the new target for remaining lines.
        """
        undef = StepSegment(
            step="undefined",
            description=warning_msg,
            status="warning",
        )
        if step_stack:
            parent_step, parent_path = step_stack[-1]
            parent_step.steps.append(undef)
            undef_path = parent_path + ["undefined"]
        else:
            block.steps.append(undef)
            undef_path = ["undefined"]
        parser_warnings.append(warning_msg)
        return undef, undef_path

    def _dispatch_content_event(
        line: str,
        entry: dict[str, Any],
        event_type: str,
        target_step: StepSegment,
        target_path: list[str],
    ) -> None:
        """Dispatch a content event to a step and bubble to block."""
        target_step.logs = _append_to_logs(target_step.logs, line)

        if event_type == "feature":
            name = entry.get("name", "")
            feat: dict[str, Any] = {"name": name}
            if not use_assertions:
                feat["block"] = block.block
            action = entry.get("action")
            if action is not None:
                feat["action"] = action
            _copy_source(feat, entry)
            target_step.features.append(feat)
            qualified = copy.copy(feat)
            qualified["name"] = _build_step_qualified_name(
                target_path, name,
            )
            block.features.append(qualified)

        elif event_type == "measurement":
            name = entry.get("name", "")
            value = entry.get("value")
            m: dict[str, Any] = {"name": name, "value": value}
            if not use_assertions:
                m["block"] = block.block
            unit = entry.get("unit")
            if unit is not None:
                m["unit"] = unit
            _copy_source(m, entry)
            target_step.measurements.append(m)
            qualified_m = copy.copy(m)
            qualified_m["name"] = _build_step_qualified_name(
                target_path, name,
            )
            block.measurements.append(qualified_m)

        elif event_type == "result":
            if use_assertions:
                assertion = _normalize_assertion(entry)
                target_step.assertions.append(assertion)
                qualified_a = copy.copy(assertion)
                desc = assertion.get("description", "")
                if desc:
                    qualified_a["description"] = _build_step_qualified_name(
                        target_path, desc,
                    )
                block.assertions.append(qualified_a)
            else:
                status = entry.get("status", "")
                message = entry.get("message", "")
                block_tag = block.block
                r: dict[str, Any] = {
                    "status": status, "message": message,
                    "block": block_tag,
                }
                _copy_source(r, entry)
                target_step.results.append(r)
                qualified_r = copy.copy(r)
                if message:
                    qualified_r["message"] = _build_step_qualified_name(
                        target_path, message,
                    )
                block.results.append(qualified_r)

        elif event_type == "error":
            message = entry.get("message", "")
            if use_assertions:
                err: dict[str, Any] = {"message": message}
            else:
                block_tag = block.block
                err = {"message": message, "block": block_tag}
            _copy_source(err, entry)
            target_step.errors.append(err)
            block.errors.append(err)
            # Propagate "failed" status up the step stack
            for ancestor_step, _ in step_stack:
                ancestor_step.status = "failed"
            # "failed" takes precedence over "warning"
            if target_step.status == "warning":
                target_step.status = "failed"

    def _dispatch_block_content(
        line: str,
        entry: dict[str, Any],
        event_type: str,
    ) -> None:
        """Dispatch a content event to the block (outside any step)."""
        block.logs = _append_to_logs(block.logs, line)

        if event_type == "feature":
            name = entry.get("name", "")
            feat_b: dict[str, Any] = {"name": name}
            if not use_assertions:
                feat_b["block"] = block.block
            action = entry.get("action")
            if action is not None:
                feat_b["action"] = action
            _copy_source(feat_b, entry)
            block.features.append(feat_b)

        elif event_type == "measurement":
            name = entry.get("name", "")
            value = entry.get("value")
            m_b: dict[str, Any] = {"name": name, "value": value}
            if not use_assertions:
                m_b["block"] = block.block
            unit = entry.get("unit")
            if unit is not None:
                m_b["unit"] = unit
            _copy_source(m_b, entry)
            block.measurements.append(m_b)

        elif event_type == "result":
            if use_assertions:
                block.assertions.append(_normalize_assertion(entry))
            else:
                status = entry.get("status", "")
                message = entry.get("message", "")
                block_tag = block.block
                r_b: dict[str, Any] = {
                    "status": status, "message": message,
                    "block": block_tag,
                }
                _copy_source(r_b, entry)
                block.results.append(r_b)

        elif event_type == "error":
            message = entry.get("message", "")
            err_b: dict[str, Any] = {"message": message}
            if not use_assertions:
                err_b["block"] = block.block
            _copy_source(err_b, entry)
            block.errors.append(err_b)

    # Whether we are currently collecting remainder into an undefined step
    # at the block scope level (after a block-level structural error).
    collecting_remainder: StepSegment | None = None
    collecting_remainder_path: list[str] = []

    for line in raw_lines:
        # If collecting remainder at block scope, redirect everything there
        if collecting_remainder is not None and not step_stack:
            if not line.startswith(SENTINEL):
                collecting_remainder.logs = _append_to_logs(
                    collecting_remainder.logs, line,
                )
                continue
            json_str = line[len(SENTINEL):]
            try:
                entry = json.loads(json_str)
            except (json.JSONDecodeError, TypeError):
                collecting_remainder.logs = _append_to_logs(
                    collecting_remainder.logs, line,
                )
                continue
            if not isinstance(entry, dict):
                collecting_remainder.logs = _append_to_logs(
                    collecting_remainder.logs, line,
                )
                continue
            event_type = entry.get("type")
            if event_type in ("block_start", "block_end", "step_end"):
                # Skip structural markers in remainder
                continue
            if event_type == "step_start":
                # Nested step_start in remainder -- still just collect
                collecting_remainder.logs = _append_to_logs(
                    collecting_remainder.logs, line,
                )
                continue
            # Content event in remainder
            _dispatch_content_event(
                line, entry, event_type or "",
                collecting_remainder, collecting_remainder_path,
            )
            continue

        if not line.startswith(SENTINEL):
            # Plain text line
            inner = _innermost_step()
            if inner is not None:
                step_seg, _ = inner
                step_seg.logs = _append_to_logs(step_seg.logs, line)
            else:
                block.logs = _append_to_logs(block.logs, line)
            continue

        json_str = line[len(SENTINEL):]
        try:
            entry = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            # Malformed sentinel -- treat as plain text
            inner = _innermost_step()
            if inner is not None:
                step_seg, _ = inner
                step_seg.logs = _append_to_logs(step_seg.logs, line)
            else:
                block.logs = _append_to_logs(block.logs, line)
            continue

        if not isinstance(entry, dict):
            inner = _innermost_step()
            if inner is not None:
                step_seg, _ = inner
                step_seg.logs = _append_to_logs(step_seg.logs, line)
            else:
                block.logs = _append_to_logs(block.logs, line)
            continue

        event_type = entry.get("type")

        if event_type == "step_start":
            step_name = entry.get("step", "")
            description = entry.get("description", "")

            # Case 4: Duplicate step names within same scope
            scope_id = _current_scope_id()
            if scope_id in scope_names and step_name in scope_names[scope_id]:
                # Duplicate name -- create undefined step for remainder
                undef, undef_path = _create_undefined_step(
                    f"duplicate step name '{step_name}' in same scope"
                )
                if step_stack:
                    # Inside a step -- push the undefined step so
                    # subsequent lines go there
                    step_stack.append((undef, undef_path))
                else:
                    # At block scope -- enter block-level remainder mode
                    collecting_remainder = undef
                    collecting_remainder_path = undef_path
                continue

            new_step = StepSegment(step=step_name, description=description)
            step_path = (
                [s.step for s, _ in step_stack] + [step_name]
                if step_stack
                else [step_name]
            )

            if step_stack:
                parent_step, _ = step_stack[-1]
                parent_step.steps.append(new_step)
            else:
                block.steps.append(new_step)

            step_stack.append((new_step, step_path))
            # Track name in scope
            if scope_id not in scope_names:
                scope_names[scope_id] = set()
            # The parent scope tracks this name (before pushing)
            scope_names[scope_id].add(step_name)
            # Initialize scope for the new step's children
            scope_names[id(new_step)] = set()

        elif event_type == "step_end":
            step_name = entry.get("step", "")
            if step_stack:
                current_step, _ = step_stack[-1]
                if current_step.step == step_name:
                    step_stack.pop()
                else:
                    # Case 1: step_end name mismatch
                    # Keep valid prefix, pop the mismatched step first,
                    # then create undefined step in the parent scope
                    # for the remainder.
                    step_stack.pop()
                    undef, undef_path = _create_undefined_step(
                        f"step_end name mismatch: expected "
                        f"'{current_step.step}', got '{step_name}'"
                    )
                    if step_stack:
                        # Push undefined as collector for remainder
                        step_stack.append((undef, undef_path))
                    else:
                        collecting_remainder = undef
                        collecting_remainder_path = undef_path
            else:
                parser_warnings.append(
                    f"step_end '{step_name}' without matching step_start"
                )

        elif event_type in ("block_start", "block_end"):
            # Block markers within the block's lines are structural;
            # they don't produce content.  Skip them.
            pass

        else:
            # Content event -- attribute to innermost step or block
            inner = _innermost_step()

            if inner is not None:
                step_seg, step_path = inner
                _dispatch_content_event(
                    line, entry, event_type or "",
                    step_seg, step_path,
                )
            else:
                _dispatch_block_content(line, entry, event_type or "")

    # Case 3: block_end with unclosed steps.
    # If any steps are still open, collect their unclosed content into
    # an undefined step.
    if step_stack:
        # The step_stack still has open steps.  They were not properly
        # closed.  Create an undefined step to signal the issue.
        # The open steps themselves already contain their content as valid
        # prefix.  We just need to record the warning.
        for open_step, _ in step_stack:
            if open_step.status == "passed":
                open_step.status = "warning"
            parser_warnings.append(
                f"step '{open_step.step}' was never closed (block ended)"
            )
        step_stack.clear()

    # Strip trailing whitespace from step logs
    for step_seg in block.steps:
        _finalize_step_logs(step_seg)


def _finalize_step_logs(step: StepSegment) -> None:
    """Strip trailing whitespace from step logs recursively."""
    step.logs = step.logs.strip("\n")
    for sub in step.steps:
        _finalize_step_logs(sub)


def parse_test_output(
    lines: list[str] | str,
) -> ParsedOutput:
    """Parse structured test output from stdout lines.

    Scans each line for the ``[TST]`` sentinel prefix.  Lines with the
    prefix are parsed as JSON events and dispatched into ``BlockSegment``
    objects.  Lines without the prefix become part of the active block's
    ``logs`` or an ``"untyped"`` block.

    Args:
        lines: List of output lines, or a single string (split on newlines).

    Returns:
        A :class:`ParsedOutput` with events grouped by block.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()

    all_segments: list[BlockSegment] = []
    parser_warnings: list[str] = []
    text_accum: list[str] = []
    current_block: BlockSegment | None = None
    # Track raw lines for step post-processing
    block_raw_lines: list[str] = []

    def _flush_text() -> None:
        if text_accum:
            seg = BlockSegment(block="untyped", logs="\n".join(text_accum))
            all_segments.append(seg)
            text_accum.clear()

    def _flush_block() -> None:
        nonlocal current_block
        if current_block is not None:
            _finalize_block(current_block)
            # Post-process for steps
            _parse_steps_in_block(
                current_block, block_raw_lines, parser_warnings,
            )
            all_segments.append(current_block)
            current_block = None
            block_raw_lines.clear()

    for line in lines:
        if not line.startswith(SENTINEL):
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
                block_raw_lines.append(line)
            else:
                text_accum.append(line)
            continue

        json_str = line[len(SENTINEL):]

        try:
            entry = json.loads(json_str)
        except json.JSONDecodeError:
            parser_warnings.append(
                f"malformed [TST] line, skipping: {line}"
            )
            continue

        if not isinstance(entry, dict):
            parser_warnings.append(
                f"[TST] line is not a JSON object, skipping: {line}"
            )
            continue

        event_type = entry.get("type")
        if event_type is None:
            parser_warnings.append(
                f"[TST] line missing type field, skipping: {line}"
            )
            continue

        if event_type in ("phase", "block_start"):
            block_name = entry.get("block")
            if block_name is not None:
                _flush_block()
                _flush_text()
                current_block = BlockSegment(
                    block=block_name,
                    description=entry.get("description", ""),
                )
                block_raw_lines = []

        elif event_type == "block_end":
            _flush_block()

        elif event_type == "step_start" and current_block is None:
            # Case 2: step_start outside any block — create undefined block
            _flush_text()
            parser_warnings.append(
                "step_start outside any block: creating undefined block"
            )
            current_block = BlockSegment(block="undefined")
            block_raw_lines = [line]
            if current_block.logs:
                current_block.logs += "\n"
            current_block.logs += line

        else:
            # Content event — preserve raw line in block logs
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
                block_raw_lines.append(line)

            if event_type == "feature":
                name = entry.get("name", "")
                block_tag = current_block.block if current_block else None
                feat: dict[str, Any] = {"name": name, "block": block_tag}
                _copy_source(feat, entry)
                if current_block is not None:
                    current_block.features.append(feat)
                else:
                    # Event before any block — buffer in untyped
                    _flush_text()
                    seg = BlockSegment(block="untyped", logs=line)
                    seg.features.append(feat)
                    all_segments.append(seg)

            elif event_type == "measurement":
                name = entry.get("name", "")
                value = entry.get("value")
                block_tag = current_block.block if current_block else None
                m: dict[str, Any] = {"name": name, "value": value, "block": block_tag}
                _copy_source(m, entry)
                if current_block is not None:
                    current_block.measurements.append(m)
                else:
                    _flush_text()
                    seg = BlockSegment(block="untyped", logs=line)
                    seg.measurements.append(m)
                    all_segments.append(seg)

            elif event_type == "result":
                status = entry.get("status", "")
                message = entry.get("message", "")
                block_tag = current_block.block if current_block else None
                r: dict[str, Any] = {"status": status, "message": message, "block": block_tag}
                _copy_source(r, entry)
                if current_block is not None:
                    current_block.results.append(r)
                else:
                    _flush_text()
                    seg = BlockSegment(block="untyped", logs=line)
                    seg.results.append(r)
                    all_segments.append(seg)

            elif event_type == "error":
                message = entry.get("message", "")
                block_tag = current_block.block if current_block else None
                e: dict[str, Any] = {"message": message, "block": block_tag}
                _copy_source(e, entry)
                if current_block is not None:
                    current_block.errors.append(e)
                else:
                    _flush_text()
                    seg = BlockSegment(block="untyped", logs=line)
                    seg.errors.append(e)
                    all_segments.append(seg)

            # else: unknown type — already in logs, skip silently

    _flush_block()
    _flush_text()

    # Categorise segments into rigging / run_blocks / verdict.
    rigging: BlockSegment | None = None
    verdict: BlockSegment | None = None
    run_blocks: list[BlockSegment] = []

    for seg in all_segments:
        if seg.block == "rigging" and rigging is None:
            rigging = seg
        elif seg.block == "verdict":
            # Always take the last verdict; push any earlier one to run_blocks
            if verdict is not None:
                run_blocks.append(verdict)
            verdict = seg
        else:
            run_blocks.append(seg)

    return ParsedOutput(
        rigging=rigging,
        run_blocks=run_blocks,
        verdict=verdict,
        warnings=parser_warnings,
    )


def is_rigging_failure(parsed: ParsedOutput) -> bool:
    """Determine if the parsed output represents a rigging failure.

    A rigging failure is an error that occurs during the rigging phase,
    meaning the test did not run (as opposed to a test failure).

    Args:
        parsed: Output from parse_test_output().

    Returns:
        True if errors occurred during rigging phase.
    """
    return parsed.has_rigging_failure


def get_rigging_features(parsed: ParsedOutput) -> list[str]:
    """Extract feature names declared during rigging.

    Args:
        parsed: Output from parse_test_output().

    Returns:
        List of feature name strings from rigging phase.
    """
    if parsed.rigging is None:
        return []
    return [f["name"] for f in parsed.rigging.features]


# ---------------------------------------------------------------------------
# Segment-based stdout parser
# ---------------------------------------------------------------------------


@dataclass
class TextSegment:
    """Plain text output not within any structured block."""

    text: str


Segment = Union[TextSegment, BlockSegment]


def _normalize_assertion(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a result event into an assertion dict.

    Handles both ``name``/``passed`` and ``status``/``message`` formats.
    Preserves ``_file`` and ``_line`` source metadata if present.
    """
    if "name" in entry:
        passed = entry.get("passed")
        if isinstance(passed, bool):
            status = "passed" if passed else "failed"
        else:
            status = str(passed) if passed is not None else "unknown"
        result: dict[str, Any] = {"description": entry["name"], "status": status}
        _copy_source(result, entry)
        return result

    status = entry.get("status", "unknown")
    message = entry.get("message", "")
    result = {"description": message, "status": status}
    _copy_source(result, entry)
    return result


def parse_stdout_segments(stdout: str) -> list[Segment]:
    """Parse test stdout into interleaved text and block segments.

    Splits stdout into a sequence of ``TextSegment`` (plain text) and
    ``BlockSegment`` (structured blocks delimited by block_start/block_end
    events).  This allows unified rendering of structured and unstructured
    test output.

    Blocks that contain ``step_start``/``step_end`` events will have their
    ``steps`` field populated with a tree of ``StepSegment`` objects via
    the shared ``_parse_steps_in_block()`` post-processor.

    Args:
        stdout: Raw stdout string from a test execution.

    Returns:
        List of segments in the order they appear in stdout.
    """
    if not stdout:
        return []

    lines = stdout.splitlines()
    segments: list[Segment] = []
    text_accum: list[str] = []
    current_block: BlockSegment | None = None
    # Track raw lines for step post-processing
    block_raw_lines: list[str] = []
    # Warnings from step parsing (not exposed but needed by helper)
    parser_warnings: list[str] = []

    def _flush_text() -> None:
        if text_accum:
            segments.append(TextSegment(text="\n".join(text_accum)))
            text_accum.clear()

    def _flush_block() -> None:
        nonlocal current_block
        if current_block is not None:
            _finalize_block(current_block)
            # Post-process for steps (using assertion mode)
            _parse_steps_in_block(
                current_block, block_raw_lines, parser_warnings,
                use_assertions=True,
            )
            segments.append(current_block)
            current_block = None
            block_raw_lines.clear()

    for line in lines:
        if not line.startswith(SENTINEL):
            # Plain text line
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
                block_raw_lines.append(line)
            else:
                text_accum.append(line)
            continue

        json_str = line[len(SENTINEL):]
        try:
            entry = json.loads(json_str)
        except json.JSONDecodeError:
            # Malformed sentinel line -- treat as plain text
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
                block_raw_lines.append(line)
            else:
                text_accum.append(line)
            continue

        if not isinstance(entry, dict) or "type" not in entry:
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
                block_raw_lines.append(line)
            else:
                text_accum.append(line)
            continue

        event_type = entry["type"]

        if event_type in ("phase", "block_start"):
            block_name = entry.get("block")
            if block_name is not None:
                # Implicitly close previous block if still open
                _flush_block()
                _flush_text()
                current_block = BlockSegment(
                    block=block_name,
                    description=entry.get("description", ""),
                )
                block_raw_lines = []

        elif event_type == "block_end":
            _flush_block()

        elif event_type == "step_start" and current_block is None:
            # step_start outside any block -- create undefined block
            _flush_text()
            parser_warnings.append(
                "step_start outside any block: creating undefined block"
            )
            current_block = BlockSegment(block="undefined")
            block_raw_lines = [line]
            if current_block.logs:
                current_block.logs += "\n"
            current_block.logs += line

        elif current_block is not None:
            # Content event -- preserve raw sentinel line in logs
            if current_block.logs:
                current_block.logs += "\n"
            current_block.logs += line
            block_raw_lines.append(line)

            # Dispatch to structured fields
            if event_type == "feature":
                feat: dict[str, Any] = {"name": entry.get("name", "")}
                action = entry.get("action")
                if action is not None:
                    feat["action"] = action
                _copy_source(feat, entry)
                current_block.features.append(feat)

            elif event_type == "measurement":
                m: dict[str, Any] = {
                    "name": entry.get("name", ""),
                    "value": entry.get("value"),
                }
                unit = entry.get("unit")
                if unit is not None:
                    m["unit"] = unit
                _copy_source(m, entry)
                current_block.measurements.append(m)

            elif event_type == "result":
                current_block.assertions.append(_normalize_assertion(entry))

            elif event_type == "error":
                err: dict[str, Any] = {"message": entry.get("message", "")}
                _copy_source(err, entry)
                current_block.errors.append(err)

            # Unknown event types (including step_start/step_end):
            # already in logs and block_raw_lines, skip silently.
            # Step events will be processed by _parse_steps_in_block().

        # Events outside any block (other than block_start/step_start)
        # are skipped

    # Finalize anything still open
    _flush_block()
    _flush_text()

    return segments
