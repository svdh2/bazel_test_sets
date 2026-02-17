"""Structured test log parser.

Parses [TST] sentinel lines from test stdout, dispatching on the type
field to extract block phases, features, measurements, results, and errors.
Unknown types and malformed lines are skipped for forward compatibility.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from typing import Any, Union

# Sentinel prefix for structured log lines
SENTINEL = "[TST] "


@dataclass
class ParsedTestOutput:
    """Result of parsing structured log output from a test."""

    block_sequence: list[str] = field(default_factory=list)
    features: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    plain_output: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_test_output(
    lines: list[str] | str,
) -> dict[str, Any]:
    """Parse structured test output from stdout lines.

    Scans each line for the [TST] sentinel prefix. Lines with the prefix
    are parsed as JSON events. Lines without the prefix are collected as
    plain output. Unknown event types are skipped for forward compatibility.

    Args:
        lines: List of output lines, or a single string (split on newlines).

    Returns:
        Dictionary with:
        - block_sequence: List of block names from phase events
        - features: List of feature dicts (name, block)
        - measurements: List of measurement dicts (name, value, block)
        - results: List of result dicts (status, message, block)
        - errors: List of error dicts (message, block)
        - plain_output: Non-sentinel lines
        - warnings: Parser warnings for malformed lines
    """
    if isinstance(lines, str):
        lines = lines.splitlines()

    parsed = ParsedTestOutput()
    current_block: str | None = None

    for line in lines:
        if not line.startswith(SENTINEL):
            parsed.plain_output.append(line)
            continue

        json_str = line[len(SENTINEL) :]

        try:
            entry = json.loads(json_str)
        except json.JSONDecodeError:
            parsed.warnings.append(f"malformed [TST] line, skipping: {line}")
            continue

        if not isinstance(entry, dict):
            parsed.warnings.append(
                f"[TST] line is not a JSON object, skipping: {line}"
            )
            continue

        event_type = entry.get("type")
        if event_type is None:
            parsed.warnings.append(
                f"[TST] line missing type field, skipping: {line}"
            )
            continue

        if event_type in ("phase", "block_start"):
            block = entry.get("block")
            if block is not None:
                current_block = block
                parsed.block_sequence.append(block)

        elif event_type == "block_end":
            # End of a block phase; reset current_block
            current_block = None

        elif event_type == "feature":
            name = entry.get("name", "")
            parsed.features.append(
                {"name": name, "block": current_block}
            )

        elif event_type == "measurement":
            name = entry.get("name", "")
            value = entry.get("value")
            parsed.measurements.append(
                {"name": name, "value": value, "block": current_block}
            )

        elif event_type == "result":
            status = entry.get("status", "")
            message = entry.get("message", "")
            parsed.results.append(
                {
                    "status": status,
                    "message": message,
                    "block": current_block,
                }
            )

        elif event_type == "error":
            message = entry.get("message", "")
            parsed.errors.append(
                {"message": message, "block": current_block}
            )

        else:
            # Unknown type - skip for forward compatibility
            pass

    # Compute derived flags
    has_rigging_failure = any(
        e.get("block") == "rigging" if isinstance(e, dict) else False
        for e in parsed.errors
    )

    return {
        "block_sequence": parsed.block_sequence,
        "features": parsed.features,
        "measurements": parsed.measurements,
        "results": parsed.results,
        "errors": parsed.errors,
        "plain_output": parsed.plain_output,
        "warnings": parsed.warnings,
        "has_rigging_failure": has_rigging_failure,
    }


def is_rigging_failure(parsed: dict[str, Any]) -> bool:
    """Determine if the parsed output represents a rigging failure.

    A rigging failure is an error that occurs during the rigging phase,
    meaning the test did not run (as opposed to a test failure).

    Args:
        parsed: Output from parse_test_output().

    Returns:
        True if errors occurred during rigging phase.
    """
    for error in parsed.get("errors", []):
        if error.get("block") == "rigging":
            return True
    return False


def get_rigging_features(parsed: dict[str, Any]) -> list[str]:
    """Extract feature names declared during rigging.

    Args:
        parsed: Output from parse_test_output().

    Returns:
        List of feature name strings from rigging phase.
    """
    return [
        f["name"]
        for f in parsed.get("features", [])
        if f.get("block") == "rigging"
    ]


# ---------------------------------------------------------------------------
# Segment-based stdout parser
# ---------------------------------------------------------------------------


@dataclass
class TextSegment:
    """Plain text output not within any structured block."""

    text: str


@dataclass
class BlockSegment:
    """A structured block delimited by block_start/block_end events."""

    block: str
    description: str = ""
    logs: str = ""
    error: str | None = None
    features: list[dict[str, Any]] = field(default_factory=list)
    measurements: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)


Segment = Union[TextSegment, BlockSegment]


def _finalize_block(seg: BlockSegment) -> BlockSegment:
    """Strip trailing whitespace from accumulated logs."""
    seg.logs = seg.logs.strip("\n")
    return seg


def _normalize_assertion(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a result event into an assertion dict.

    Handles both ``name``/``passed`` and ``status``/``message`` formats.
    """
    if "name" in entry:
        passed = entry.get("passed")
        if isinstance(passed, bool):
            status = "passed" if passed else "failed"
        else:
            status = str(passed) if passed is not None else "unknown"
        return {"description": entry["name"], "status": status}

    status = entry.get("status", "unknown")
    message = entry.get("message", "")
    return {"description": message, "status": status}


def parse_stdout_segments(stdout: str) -> list[Segment]:
    """Parse test stdout into interleaved text and block segments.

    Splits stdout into a sequence of ``TextSegment`` (plain text) and
    ``BlockSegment`` (structured blocks delimited by block_start/block_end
    events).  This allows unified rendering of structured and unstructured
    test output.

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

    def _flush_text() -> None:
        if text_accum:
            segments.append(TextSegment(text="\n".join(text_accum)))
            text_accum.clear()

    def _flush_block() -> None:
        nonlocal current_block
        if current_block is not None:
            segments.append(_finalize_block(current_block))
            current_block = None

    for line in lines:
        if not line.startswith(SENTINEL):
            # Plain text line
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
            else:
                text_accum.append(line)
            continue

        json_str = line[len(SENTINEL):]
        try:
            entry = json.loads(json_str)
        except json.JSONDecodeError:
            # Malformed sentinel line — treat as plain text
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
            else:
                text_accum.append(line)
            continue

        if not isinstance(entry, dict) or "type" not in entry:
            if current_block is not None:
                if current_block.logs:
                    current_block.logs += "\n"
                current_block.logs += line
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

        elif event_type == "block_end":
            _flush_block()

        elif current_block is not None:
            # Event inside a block — dispatch to block fields
            if event_type == "feature":
                feat: dict[str, Any] = {"name": entry.get("name", "")}
                action = entry.get("action")
                if action is not None:
                    feat["action"] = action
                current_block.features.append(feat)

            elif event_type == "measurement":
                m: dict[str, Any] = {
                    "name": entry.get("name", ""),
                    "value": entry.get("value"),
                }
                unit = entry.get("unit")
                if unit is not None:
                    m["unit"] = unit
                current_block.measurements.append(m)

            elif event_type == "result":
                current_block.assertions.append(_normalize_assertion(entry))

            elif event_type == "error":
                current_block.error = entry.get("message", "")

            # Unknown event types inside blocks are silently skipped

        # Events outside any block (other than block_start) are skipped

    # Finalize anything still open
    _flush_block()
    _flush_text()

    return segments
