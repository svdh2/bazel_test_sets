"""Tests for the structured test log parser."""

from __future__ import annotations

import pytest

from orchestrator.analysis.log_parser import (
    SENTINEL,
    BlockSegment,
    ParsedOutput,
    StepSegment,
    TextSegment,
    parse_test_output,
    parse_stdout_segments,
    is_rigging_failure,
    get_rigging_features,
)


class TestPhaseEvents:
    """Tests for phase event parsing."""

    def test_single_phase(self):
        """Parse a single phase event."""
        lines = ['[TST] {"type": "phase", "block": "rigging"}']
        result = parse_test_output(lines)
        assert result.block_sequence == ["rigging"]

    def test_multiple_phases(self):
        """Parse multiple phase events in order."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "phase", "block": "stimulation"}',
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "phase", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        assert result.block_sequence == [
            "rigging",
            "stimulation",
            "checkpoint",
            "verdict",
        ]

    def test_phase_without_block(self):
        """Phase event without block field is ignored."""
        lines = ['[TST] {"type": "phase"}']
        result = parse_test_output(lines)
        assert result.block_sequence == []

    def test_phase_with_interleaved_output(self):
        """Phases extracted correctly when interleaved with plain output."""
        lines = [
            "Setting up test environment...",
            '[TST] {"type": "phase", "block": "rigging"}',
            "Rigging complete",
            '[TST] {"type": "phase", "block": "stimulation"}',
            "Running test...",
        ]
        result = parse_test_output(lines)
        assert result.block_sequence == ["rigging", "stimulation"]
        # Plain text before rigging goes into an untyped run_block
        assert any(
            "Setting up test environment..." in b.logs
            for b in result.run_blocks
            if b.block == "untyped"
        )
        # Plain text inside rigging goes into rigging's logs
        assert result.rigging is not None
        assert "Rigging complete" in result.rigging.logs


class TestBlockStructure:
    """Tests for the block-oriented return structure."""

    def test_rigging_extracted(self):
        """Rigging block is extracted into the rigging field."""
        lines = [
            '[TST] {"type": "block_start", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "auth"}',
            '[TST] {"type": "block_end", "block": "rigging"}',
        ]
        result = parse_test_output(lines)
        assert result.rigging is not None
        assert result.rigging.block == "rigging"
        assert len(result.rigging.features) == 1

    def test_verdict_extracted(self):
        """Verdict block is extracted into the verdict field."""
        lines = [
            '[TST] {"type": "block_start", "block": "verdict"}',
            '[TST] {"type": "result", "status": "pass", "message": "ok"}',
            '[TST] {"type": "block_end", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        assert result.verdict is not None
        assert result.verdict.block == "verdict"
        assert len(result.verdict.results) == 1

    def test_stimulation_in_run_blocks(self):
        """Stimulation blocks go into run_blocks."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "measurement", "name": "x", "value": 1}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)
        assert result.rigging is None
        assert result.verdict is None
        typed_blocks = [b for b in result.run_blocks if b.block != "untyped"]
        assert len(typed_blocks) == 1
        assert typed_blocks[0].block == "stimulation"

    def test_checkpoint_in_run_blocks(self):
        """Checkpoint blocks go into run_blocks."""
        lines = [
            '[TST] {"type": "block_start", "block": "checkpoint"}',
            '[TST] {"type": "result", "status": "pass", "message": "ok"}',
            '[TST] {"type": "block_end", "block": "checkpoint"}',
        ]
        result = parse_test_output(lines)
        typed_blocks = [b for b in result.run_blocks if b.block != "untyped"]
        assert len(typed_blocks) == 1
        assert typed_blocks[0].block == "checkpoint"

    def test_untyped_block_before_rigging(self):
        """Text before rigging creates an untyped block in run_blocks."""
        lines = [
            "=== Test banner ===",
            '[TST] {"type": "block_start", "block": "rigging"}',
            '[TST] {"type": "block_end", "block": "rigging"}',
        ]
        result = parse_test_output(lines)
        assert result.rigging is not None
        untyped = [b for b in result.run_blocks if b.block == "untyped"]
        assert len(untyped) == 1
        assert "=== Test banner ===" in untyped[0].logs

    def test_untyped_block_between_blocks(self):
        """Text between typed blocks creates an untyped block."""
        lines = [
            '[TST] {"type": "block_start", "block": "rigging"}',
            '[TST] {"type": "block_end", "block": "rigging"}',
            "Some interstitial text",
            '[TST] {"type": "block_start", "block": "verdict"}',
            '[TST] {"type": "block_end", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        untyped = [b for b in result.run_blocks if b.block == "untyped"]
        assert len(untyped) == 1
        assert "Some interstitial text" in untyped[0].logs

    def test_untyped_block_after_verdict(self):
        """Text after verdict creates an untyped block in run_blocks."""
        lines = [
            '[TST] {"type": "block_start", "block": "verdict"}',
            '[TST] {"type": "block_end", "block": "verdict"}',
            "Test complete.",
        ]
        result = parse_test_output(lines)
        untyped = [b for b in result.run_blocks if b.block == "untyped"]
        assert len(untyped) == 1
        assert "Test complete." in untyped[0].logs

    def test_all_blocks_property(self):
        """all_blocks returns blocks in order: rigging, run_blocks, verdict."""
        lines = [
            '[TST] {"type": "block_start", "block": "rigging"}',
            '[TST] {"type": "block_end", "block": "rigging"}',
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
            '[TST] {"type": "block_start", "block": "verdict"}',
            '[TST] {"type": "block_end", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        block_names = [b.block for b in result.all_blocks]
        assert block_names == ["rigging", "stimulation", "verdict"]


class TestFeatureEvents:
    """Tests for feature event parsing."""

    def test_feature_in_rigging(self):
        """Feature event during rigging phase is captured with block."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "user_auth"}',
        ]
        result = parse_test_output(lines)
        assert len(result.all_features) == 1
        assert result.all_features[0]["name"] == "user_auth"
        assert result.all_features[0]["block"] == "rigging"

    def test_feature_outside_rigging(self):
        """Feature event outside rigging is still captured with its block."""
        lines = [
            '[TST] {"type": "phase", "block": "stimulation"}',
            '[TST] {"type": "feature", "name": "some_feature"}',
        ]
        result = parse_test_output(lines)
        assert len(result.all_features) == 1
        assert result.all_features[0]["block"] == "stimulation"

    def test_feature_before_any_phase(self):
        """Feature event before any phase has block=None."""
        lines = ['[TST] {"type": "feature", "name": "early_feature"}']
        result = parse_test_output(lines)
        assert len(result.all_features) == 1
        assert result.all_features[0]["block"] is None

    def test_feature_without_name(self):
        """Feature event without name defaults to empty string."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature"}',
        ]
        result = parse_test_output(lines)
        assert result.all_features[0]["name"] == ""

    def test_multiple_features(self):
        """Multiple feature events are all captured."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "auth"}',
            '[TST] {"type": "feature", "name": "billing"}',
            '[TST] {"type": "feature", "name": "notifications"}',
        ]
        result = parse_test_output(lines)
        assert len(result.all_features) == 3
        names = [f["name"] for f in result.all_features]
        assert names == ["auth", "billing", "notifications"]


class TestMeasurementEvents:
    """Tests for measurement event parsing."""

    def test_simple_measurement(self):
        """Parse a simple measurement with name and value."""
        lines = [
            '[TST] {"type": "measurement", "name": "response_time", "value": 142.0}'
        ]
        result = parse_test_output(lines)
        assert len(result.all_measurements) == 1
        assert result.all_measurements[0]["name"] == "response_time"
        assert result.all_measurements[0]["value"] == 142.0

    def test_measurement_with_structured_value(self):
        """Parse a measurement with a structured (dict) value."""
        lines = [
            '[TST] {"type": "measurement", "name": "response_time", '
            '"value": {"value": 142.0, "unit": "ms"}}'
        ]
        result = parse_test_output(lines)
        assert result.all_measurements[0]["value"] == {
            "value": 142.0,
            "unit": "ms",
        }

    def test_measurement_without_value(self):
        """Measurement without value field has value=None."""
        lines = ['[TST] {"type": "measurement", "name": "counter"}']
        result = parse_test_output(lines)
        assert result.all_measurements[0]["value"] is None

    def test_measurement_tracks_block(self):
        """Measurement records the current block."""
        lines = [
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "measurement", "name": "latency", "value": 50}',
        ]
        result = parse_test_output(lines)
        assert result.all_measurements[0]["block"] == "checkpoint"


class TestResultEvents:
    """Tests for result event parsing."""

    def test_pass_result(self):
        """Parse a passing result event."""
        lines = [
            '[TST] {"type": "phase", "block": "verdict"}',
            '[TST] {"type": "result", "status": "pass", "message": "all checks passed"}',
        ]
        result = parse_test_output(lines)
        assert len(result.all_results) == 1
        assert result.all_results[0]["status"] == "pass"
        assert result.all_results[0]["message"] == "all checks passed"
        assert result.all_results[0]["block"] == "verdict"

    def test_fail_result(self):
        """Parse a failing result event."""
        lines = [
            '[TST] {"type": "result", "status": "fail", "message": "assertion failed"}'
        ]
        result = parse_test_output(lines)
        assert result.all_results[0]["status"] == "fail"

    def test_result_without_fields(self):
        """Result without status/message defaults to empty strings."""
        lines = ['[TST] {"type": "result"}']
        result = parse_test_output(lines)
        assert result.all_results[0]["status"] == ""
        assert result.all_results[0]["message"] == ""


class TestErrorEvents:
    """Tests for error event parsing."""

    def test_error_in_rigging(self):
        """Error during rigging phase is tagged with rigging block."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "error", "message": "cannot connect to database"}',
        ]
        result = parse_test_output(lines)
        assert len(result.all_errors) == 1
        assert result.all_errors[0]["message"] == "cannot connect to database"
        assert result.all_errors[0]["block"] == "rigging"

    def test_error_without_message(self):
        """Error without message defaults to empty string."""
        lines = ['[TST] {"type": "error"}']
        result = parse_test_output(lines)
        assert result.all_errors[0]["message"] == ""

    def test_error_outside_block(self):
        """Error before any phase has block=None."""
        lines = ['[TST] {"type": "error", "message": "startup failure"}']
        result = parse_test_output(lines)
        assert result.all_errors[0]["block"] is None


class TestMalformedInput:
    """Tests for malformed input handling."""

    def test_invalid_json(self):
        """Malformed JSON after sentinel produces a warning."""
        lines = ["[TST] not json at all"]
        result = parse_test_output(lines)
        assert len(result.warnings) == 1
        assert "malformed" in result.warnings[0]
        assert result.block_sequence == []

    def test_non_object_json(self):
        """JSON that is not an object produces a warning."""
        lines = ['[TST] [1, 2, 3]']
        result = parse_test_output(lines)
        assert len(result.warnings) == 1
        assert "not a JSON object" in result.warnings[0]

    def test_missing_type_field(self):
        """JSON object without type field produces a warning."""
        lines = ['[TST] {"name": "something"}']
        result = parse_test_output(lines)
        assert len(result.warnings) == 1
        assert "missing type" in result.warnings[0]

    def test_malformed_does_not_affect_valid_lines(self):
        """Valid lines after malformed lines are still parsed correctly."""
        lines = [
            "[TST] not json",
            '[TST] {"type": "phase", "block": "verdict"}',
            '[TST] {"no_type": true}',
            '[TST] {"type": "result", "status": "pass", "message": "ok"}',
        ]
        result = parse_test_output(lines)
        assert result.block_sequence == ["verdict"]
        assert len(result.all_results) == 1
        assert result.all_results[0]["status"] == "pass"
        assert len(result.warnings) == 2

    def test_empty_sentinel_line(self):
        """Sentinel prefix with empty content produces a warning."""
        lines = ["[TST] "]
        result = parse_test_output(lines)
        assert len(result.warnings) == 1


class TestUnknownTypes:
    """Tests for unknown type handling (forward compatibility)."""

    def test_unknown_type_skipped(self):
        """Unknown event type is silently skipped."""
        lines = ['[TST] {"type": "future_event", "data": "something"}']
        result = parse_test_output(lines)
        assert result.block_sequence == []
        assert result.all_features == []
        assert result.all_measurements == []
        assert result.all_results == []
        assert result.all_errors == []
        assert result.warnings == []

    def test_unknown_type_does_not_break_state(self):
        """Unknown type does not affect current_block tracking."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "future_event", "data": "x"}',
            '[TST] {"type": "feature", "name": "auth"}',
        ]
        result = parse_test_output(lines)
        assert result.all_features[0]["block"] == "rigging"


class TestPlainOutput:
    """Tests for non-sentinel line collection."""

    def test_plain_lines_in_blocks(self):
        """Lines without sentinel inside blocks go to block logs."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            "Setting up service...",
            '[TST] {"type": "phase", "block": "stimulation"}',
            "Running test...",
        ]
        result = parse_test_output(lines)
        assert result.rigging is not None
        assert "Setting up service..." in result.rigging.logs
        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        assert len(stim) == 1
        assert "Running test..." in stim[0].logs

    def test_plain_lines_before_blocks(self):
        """Lines without sentinel before any block go to untyped block."""
        lines = [
            "Hello world",
            "Test running...",
            '[TST] {"type": "phase", "block": "rigging"}',
            "More output",
        ]
        result = parse_test_output(lines)
        untyped = [b for b in result.run_blocks if b.block == "untyped"]
        assert len(untyped) == 1
        assert "Hello world" in untyped[0].logs
        assert "Test running..." in untyped[0].logs

    def test_no_sentinel_lines(self):
        """All lines without sentinel results in empty parsed events."""
        lines = ["line 1", "line 2"]
        result = parse_test_output(lines)
        assert result.block_sequence == []
        assert result.all_features == []
        # Lines go into an untyped block
        untyped = [b for b in result.run_blocks if b.block == "untyped"]
        assert len(untyped) == 1
        assert "line 1" in untyped[0].logs

    def test_empty_input(self):
        """Empty input produces empty result."""
        result = parse_test_output([])
        assert result.block_sequence == []
        assert result.all_features == []
        assert result.all_measurements == []
        assert result.all_results == []
        assert result.all_errors == []
        assert result.run_blocks == []
        assert result.warnings == []


class TestStringInput:
    """Tests for string input handling."""

    def test_string_split_on_newlines(self):
        """String input is split on newlines."""
        text = (
            '[TST] {"type": "phase", "block": "rigging"}\n'
            "some output\n"
            '[TST] {"type": "phase", "block": "verdict"}'
        )
        result = parse_test_output(text)
        assert result.block_sequence == ["rigging", "verdict"]
        assert result.rigging is not None
        assert "some output" in result.rigging.logs

    def test_empty_string(self):
        """Empty string produces empty result."""
        result = parse_test_output("")
        assert result.block_sequence == []
        assert result.run_blocks == []


class TestFullExampleOutput:
    """Test parsing a full realistic test output."""

    def test_full_structured_test_output(self):
        """Parse a complete test output with all event types."""
        lines = [
            "=== Test: //tests:payment_flow ===",
            '[TST] {"type": "phase", "block": "rigging"}',
            "Setting up payment service...",
            '[TST] {"type": "feature", "name": "payment_gateway"}',
            '[TST] {"type": "feature", "name": "user_accounts"}',
            '[TST] {"type": "phase", "block": "stimulation"}',
            "Submitting payment request...",
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "measurement", "name": "response_time", "value": {"value": 142.0, "unit": "ms"}}',
            '[TST] {"type": "measurement", "name": "status_code", "value": 200}',
            '[TST] {"type": "result", "status": "pass", "message": "response time within SLA"}',
            '[TST] {"type": "phase", "block": "verdict"}',
            '[TST] {"type": "result", "status": "pass", "message": "payment flow verified"}',
            "Test complete.",
        ]
        result = parse_test_output(lines)

        # Verify block sequence
        assert result.block_sequence == [
            "rigging",
            "stimulation",
            "checkpoint",
            "verdict",
        ]

        # Verify features (captured in rigging block)
        assert len(result.all_features) == 2
        assert result.all_features[0] == {
            "name": "payment_gateway",
            "block": "rigging",
        }
        assert result.all_features[1] == {
            "name": "user_accounts",
            "block": "rigging",
        }
        # Rigging block directly holds features
        assert result.rigging is not None
        assert len(result.rigging.features) == 2

        # Verify measurements (in checkpoint block)
        assert len(result.all_measurements) == 2
        assert result.all_measurements[0]["name"] == "response_time"
        assert result.all_measurements[0]["value"] == {
            "value": 142.0,
            "unit": "ms",
        }
        assert result.all_measurements[0]["block"] == "checkpoint"
        assert result.all_measurements[1]["name"] == "status_code"
        assert result.all_measurements[1]["value"] == 200

        # Verify results
        assert len(result.all_results) == 2
        assert result.all_results[0]["block"] == "checkpoint"
        assert result.all_results[1]["block"] == "verdict"

        # Verify plain output is in block logs
        assert result.rigging is not None
        assert "Setting up payment service..." in result.rigging.logs

        # Text before rigging goes to untyped block
        untyped = [b for b in result.run_blocks if b.block == "untyped"]
        assert any(
            "=== Test: //tests:payment_flow ===" in b.logs for b in untyped
        )

        # No errors or warnings
        assert result.all_errors == []
        assert result.warnings == []


class TestIsRiggingFailure:
    """Tests for is_rigging_failure helper."""

    def test_rigging_error_is_failure(self):
        """Error during rigging phase is a rigging failure."""
        parsed = ParsedOutput(
            rigging=BlockSegment(
                block="rigging",
                errors=[{"message": "db down", "block": "rigging"}],
            ),
        )
        assert is_rigging_failure(parsed) is True

    def test_non_rigging_error_not_failure(self):
        """Error during non-rigging phase is not a rigging failure."""
        parsed = ParsedOutput(
            run_blocks=[
                BlockSegment(
                    block="checkpoint",
                    errors=[{"message": "assertion failed", "block": "checkpoint"}],
                ),
            ],
        )
        assert is_rigging_failure(parsed) is False

    def test_no_errors_not_failure(self):
        """No errors means not a rigging failure."""
        parsed = ParsedOutput(rigging=BlockSegment(block="rigging"))
        assert is_rigging_failure(parsed) is False

    def test_missing_rigging(self):
        """Missing rigging block means not a rigging failure."""
        parsed = ParsedOutput()
        assert is_rigging_failure(parsed) is False

    def test_mixed_errors(self):
        """One rigging error among others is still a rigging failure."""
        parsed = ParsedOutput(
            rigging=BlockSegment(
                block="rigging",
                errors=[{"message": "rigging error", "block": "rigging"}],
            ),
            run_blocks=[
                BlockSegment(
                    block="checkpoint",
                    errors=[{"message": "test error", "block": "checkpoint"}],
                ),
            ],
        )
        assert is_rigging_failure(parsed) is True


class TestGetRiggingFeatures:
    """Tests for get_rigging_features helper."""

    def test_features_in_rigging(self):
        """Extracts feature names from rigging phase."""
        parsed = ParsedOutput(
            rigging=BlockSegment(
                block="rigging",
                features=[
                    {"name": "auth", "block": "rigging"},
                    {"name": "billing", "block": "rigging"},
                ],
            ),
        )
        assert get_rigging_features(parsed) == ["auth", "billing"]

    def test_features_not_in_rigging(self):
        """Features not in rigging are excluded."""
        parsed = ParsedOutput(
            rigging=BlockSegment(
                block="rigging",
                features=[{"name": "auth", "block": "rigging"}],
            ),
            run_blocks=[
                BlockSegment(
                    block="stimulation",
                    features=[{"name": "perf", "block": "stimulation"}],
                ),
            ],
        )
        assert get_rigging_features(parsed) == ["auth"]

    def test_no_features(self):
        """No features returns empty list."""
        parsed = ParsedOutput(rigging=BlockSegment(block="rigging"))
        assert get_rigging_features(parsed) == []

    def test_missing_rigging(self):
        """Missing rigging block returns empty list."""
        parsed = ParsedOutput()
        assert get_rigging_features(parsed) == []


class TestHasRiggingFailureFlag:
    """Tests for the has_rigging_failure flag in parsed output."""

    def test_rigging_error_sets_flag(self):
        """Error during rigging phase sets has_rigging_failure=True."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "error", "message": "rigging failed"}',
        ]
        result = parse_test_output(lines)
        assert result.has_rigging_failure is True

    def test_no_error_flag_false(self):
        """No errors means has_rigging_failure=False."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "phase", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        assert result.has_rigging_failure is False

    def test_non_rigging_error_flag_false(self):
        """Error outside rigging phase has has_rigging_failure=False."""
        lines = [
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "error", "message": "test error"}',
        ]
        result = parse_test_output(lines)
        assert result.has_rigging_failure is False

    def test_empty_input_flag_false(self):
        """Empty input has has_rigging_failure=False."""
        result = parse_test_output([])
        assert result.has_rigging_failure is False


class TestParseStdoutSegments:
    """Tests for parse_stdout_segments (segment-based parser)."""

    def test_empty_input(self):
        """Empty string produces empty segment list."""
        assert parse_stdout_segments("") == []

    def test_plain_text_only(self):
        """Input with no [TST] lines produces a single TextSegment."""
        segments = parse_stdout_segments("Hello world\nTest running...")
        assert len(segments) == 1
        assert isinstance(segments[0], TextSegment)
        assert segments[0].text == "Hello world\nTest running..."

    def test_single_block(self):
        """A single block_start/block_end pair produces a BlockSegment."""
        stdout = (
            '[TST] {"type": "block_start", "block": "verdict"}\n'
            '[TST] {"type": "result", "name": "ok", "passed": true}\n'
            '[TST] {"type": "block_end", "block": "verdict"}'
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 1
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.block == "verdict"
        assert len(seg.assertions) == 1
        assert seg.assertions[0] == {"description": "ok", "status": "passed"}

    def test_interleaved_text_and_blocks(self):
        """Mixed plain text and blocks produce alternating segments."""
        stdout = (
            "Setting up...\n"
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "feature", "name": "auth"}\n'
            '[TST] {"type": "block_end", "block": "rigging"}\n'
            "Test complete."
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 3
        assert isinstance(segments[0], TextSegment)
        assert segments[0].text == "Setting up..."
        assert isinstance(segments[1], BlockSegment)
        assert segments[1].block == "rigging"
        assert isinstance(segments[2], TextSegment)
        assert segments[2].text == "Test complete."

    def test_block_with_plain_logs(self):
        """Plain text inside a block goes to BlockSegment.logs."""
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation"}\n'
            "Running test...\n"
            "Processing data...\n"
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 1
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.logs == "Running test...\nProcessing data..."

    def test_block_features(self):
        """Feature events inside a block populate features list."""
        stdout = (
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "feature", "name": "payment", "action": "connect"}\n'
            '[TST] {"type": "feature", "name": "auth"}\n'
            '[TST] {"type": "block_end", "block": "rigging"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert len(seg.features) == 2
        assert seg.features[0] == {"name": "payment", "action": "connect"}
        assert seg.features[1] == {"name": "auth"}

    def test_block_measurements(self):
        """Measurement events inside a block populate measurements list."""
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation"}\n'
            '[TST] {"type": "measurement", "name": "latency", "value": 42, "unit": "ms"}\n'
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert len(seg.measurements) == 1
        assert seg.measurements[0] == {"name": "latency", "value": 42, "unit": "ms"}

    def test_block_assertions_name_passed_format(self):
        """Result events with name/passed format normalize to assertions."""
        stdout = (
            '[TST] {"type": "block_start", "block": "verdict"}\n'
            '[TST] {"type": "result", "name": "discount_applied", "passed": true}\n'
            '[TST] {"type": "result", "name": "total_correct", "passed": false}\n'
            '[TST] {"type": "block_end", "block": "verdict"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert len(seg.assertions) == 2
        assert seg.assertions[0] == {"description": "discount_applied", "status": "passed"}
        assert seg.assertions[1] == {"description": "total_correct", "status": "failed"}

    def test_block_assertions_status_message_format(self):
        """Result events with status/message format normalize to assertions."""
        stdout = (
            '[TST] {"type": "block_start", "block": "checkpoint"}\n'
            '[TST] {"type": "result", "status": "pass", "message": "all good"}\n'
            '[TST] {"type": "block_end", "block": "checkpoint"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.assertions[0] == {"description": "all good", "status": "pass"}

    def test_block_error(self):
        """Error event populates BlockSegment.errors list."""
        stdout = (
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "error", "message": "connection refused"}\n'
            '[TST] {"type": "block_end", "block": "rigging"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert len(seg.errors) == 1
        assert seg.errors[0]["message"] == "connection refused"

    def test_description_on_block_start(self):
        """block_start with description field populates BlockSegment.description."""
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation", '
            '"description": "Apply 15% discount"}\n'
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.description == "Apply 15% discount"

    def test_implicit_block_end_on_new_block(self):
        """Starting a new block without ending the previous one finalizes it."""
        stdout = (
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "feature", "name": "auth"}\n'
            '[TST] {"type": "block_start", "block": "stimulation"}\n'
            '[TST] {"type": "measurement", "name": "x", "value": 1}\n'
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 2
        assert isinstance(segments[0], BlockSegment)
        assert segments[0].block == "rigging"
        assert len(segments[0].features) == 1
        assert isinstance(segments[1], BlockSegment)
        assert segments[1].block == "stimulation"
        assert len(segments[1].measurements) == 1

    def test_implicit_block_end_on_eof(self):
        """Reaching EOF inside a block finalizes it."""
        stdout = (
            '[TST] {"type": "block_start", "block": "verdict"}\n'
            '[TST] {"type": "result", "name": "test_ok", "passed": true}'
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 1
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.block == "verdict"
        assert len(seg.assertions) == 1

    def test_malformed_tst_lines_in_block_go_to_logs(self):
        """Malformed [TST] lines inside a block are added to logs."""
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation"}\n'
            "[TST] not valid json\n"
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert "[TST] not valid json" in seg.logs

    def test_full_realistic_output(self):
        """Parse a complete realistic test output with all event types."""
        stdout = (
            "=== Test: //ecommerce:order_test ===\n"
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "feature", "name": "order_service", "action": "initialize"}\n'
            '[TST] {"type": "feature", "name": "payment_gateway", "action": "connect"}\n'
            '[TST] {"type": "block_end", "block": "rigging"}\n'
            '[TST] {"type": "block_start", "block": "stimulation", '
            '"description": "Place order for 3 items"}\n'
            '[TST] {"type": "measurement", "name": "order_total", "value": 129.97, "unit": "USD"}\n'
            '[TST] {"type": "measurement", "name": "items_count", "value": 3, "unit": "items"}\n'
            '[TST] {"type": "block_end", "block": "stimulation"}\n'
            '[TST] {"type": "block_start", "block": "verdict"}\n'
            '[TST] {"type": "result", "name": "order_placed", "passed": true}\n'
            '[TST] {"type": "block_end", "block": "verdict"}\n'
            "Test complete."
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 5  # text, rigging, stimulation, verdict, text

        assert isinstance(segments[0], TextSegment)
        assert "order_test" in segments[0].text

        rigging = segments[1]
        assert isinstance(rigging, BlockSegment)
        assert rigging.block == "rigging"
        assert len(rigging.features) == 2

        stim = segments[2]
        assert isinstance(stim, BlockSegment)
        assert stim.block == "stimulation"
        assert stim.description == "Place order for 3 items"
        assert len(stim.measurements) == 2

        verdict = segments[3]
        assert isinstance(verdict, BlockSegment)
        assert verdict.block == "verdict"
        assert len(verdict.assertions) == 1
        assert verdict.assertions[0]["status"] == "passed"

        assert isinstance(segments[4], TextSegment)
        assert "Test complete." in segments[4].text

    def test_parse_stdout_segments_with_steps(self):
        """parse_stdout_segments populates steps within blocks.

        Verifies that step_start/step_end events are parsed into a step tree,
        that content events bubble with step-qualified names (using assertions
        instead of results), and that error recovery works.
        """
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation", '
            '"description": "Order flow"}\n'
            # Step with measurement
            '[TST] {"type": "step_start", "step": "create_order", '
            '"description": "Create a new order"}\n'
            '[TST] {"type": "measurement", "name": "order_total", '
            '"value": 129.97, "unit": "USD"}\n'
            # Nested step with assertion
            '[TST] {"type": "step_start", "step": "validate", '
            '"description": "Validate order"}\n'
            '[TST] {"type": "result", "name": "order_valid", "passed": true}\n'
            "Validation log output\n"
            '[TST] {"type": "step_end", "step": "validate"}\n'
            '[TST] {"type": "step_end", "step": "create_order"}\n'
            # Event outside steps (at block level)
            '[TST] {"type": "measurement", "name": "block_metric", '
            '"value": 42}\n'
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        assert len(segments) == 1
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.block == "stimulation"

        # Step tree: create_order -> validate
        assert len(seg.steps) == 1
        create_order = seg.steps[0]
        assert create_order.step == "create_order"
        assert create_order.description == "Create a new order"
        assert create_order.status == "passed"

        # Measurement in create_order step (original name)
        assert len(create_order.measurements) == 1
        assert create_order.measurements[0]["name"] == "order_total"
        assert create_order.measurements[0]["value"] == 129.97
        assert create_order.measurements[0]["unit"] == "USD"

        # Nested step: validate
        assert len(create_order.steps) == 1
        validate = create_order.steps[0]
        assert validate.step == "validate"
        assert validate.description == "Validate order"
        assert validate.status == "passed"

        # Assertion in validate step (normalized format)
        assert len(validate.assertions) == 1
        assert validate.assertions[0]["description"] == "order_valid"
        assert validate.assertions[0]["status"] == "passed"

        # Plain text attributed to validate step
        assert "Validation log output" in validate.logs

        # Bubbled to block: measurements use step-qualified names
        m_names = [m["name"] for m in seg.measurements]
        assert "create_order.order_total" in m_names
        assert "block_metric" in m_names

        # Bubbled to block: assertions use step-qualified descriptions
        a_descs = [a["description"] for a in seg.assertions]
        assert "create_order.validate.order_valid" in a_descs

        # Block logs should NOT contain step-internal text
        assert "Validation log output" not in seg.logs

    def test_parse_stdout_segments_steps_error_recovery(self):
        """parse_stdout_segments handles step error recovery.

        Verifies that unclosed steps get warning status and error events
        propagate failed status up the step stack.
        """
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation"}\n'
            '[TST] {"type": "step_start", "step": "risky", '
            '"description": "Risky operation"}\n'
            '[TST] {"type": "error", "message": "connection failed"}\n'
            # Step never closed -- block ends
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)

        assert len(seg.steps) == 1
        step = seg.steps[0]
        assert step.step == "risky"
        # "failed" takes precedence over "warning" (unclosed + error)
        assert step.status == "failed"
        assert len(step.errors) == 1
        assert step.errors[0]["message"] == "connection failed"

        # Error also bubbled to block
        assert len(seg.errors) == 1

    def test_parse_stdout_segments_no_steps_unchanged(self):
        """Block without steps renders identically through segment parser."""
        stdout = (
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "feature", "name": "auth", "action": "connect"}\n'
            '[TST] {"type": "block_end", "block": "rigging"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.steps == []
        assert len(seg.features) == 1
        assert seg.features[0] == {"name": "auth", "action": "connect"}


class TestSourceMetadata:
    """Tests for _file/_line source metadata preservation."""

    def test_feature_preserves_source(self):
        """Feature event preserves _file and _line in parse_test_output."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "auth", "_file": "test.py", "_line": 10}',
        ]
        result = parse_test_output(lines)
        feat = result.all_features[0]
        assert feat["_file"] == "test.py"
        assert feat["_line"] == 10

    def test_measurement_preserves_source(self):
        """Measurement event preserves _file and _line."""
        lines = [
            '[TST] {"type": "phase", "block": "stimulation"}',
            '[TST] {"type": "measurement", "name": "x", "value": 1, '
            '"_file": "test.py", "_line": 20}',
        ]
        result = parse_test_output(lines)
        m = result.all_measurements[0]
        assert m["_file"] == "test.py"
        assert m["_line"] == 20

    def test_result_preserves_source(self):
        """Result event preserves _file and _line."""
        lines = [
            '[TST] {"type": "phase", "block": "verdict"}',
            '[TST] {"type": "result", "status": "pass", "message": "ok", '
            '"_file": "test.py", "_line": 30}',
        ]
        result = parse_test_output(lines)
        r = result.all_results[0]
        assert r["_file"] == "test.py"
        assert r["_line"] == 30

    def test_error_preserves_source(self):
        """Error event preserves _file and _line."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "error", "message": "fail", '
            '"_file": "test.py", "_line": 5}',
        ]
        result = parse_test_output(lines)
        e = result.all_errors[0]
        assert e["_file"] == "test.py"
        assert e["_line"] == 5

    def test_events_without_source_have_no_keys(self):
        """Events without _file/_line do not gain those keys."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "auth"}',
        ]
        result = parse_test_output(lines)
        feat = result.all_features[0]
        assert "_file" not in feat
        assert "_line" not in feat

    def test_segments_feature_preserves_source(self):
        """parse_stdout_segments preserves _file/_line on features."""
        stdout = (
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "feature", "name": "auth", '
            '"_file": "test.py", "_line": 15}\n'
            '[TST] {"type": "block_end", "block": "rigging"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.features[0]["_file"] == "test.py"
        assert seg.features[0]["_line"] == 15

    def test_segments_measurement_preserves_source(self):
        """parse_stdout_segments preserves _file/_line on measurements."""
        stdout = (
            '[TST] {"type": "block_start", "block": "stimulation"}\n'
            '[TST] {"type": "measurement", "name": "x", "value": 1, '
            '"_file": "test.py", "_line": 22}\n'
            '[TST] {"type": "block_end", "block": "stimulation"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.measurements[0]["_file"] == "test.py"
        assert seg.measurements[0]["_line"] == 22

    def test_segments_assertion_preserves_source(self):
        """parse_stdout_segments preserves _file/_line on assertions."""
        stdout = (
            '[TST] {"type": "block_start", "block": "verdict"}\n'
            '[TST] {"type": "result", "name": "ok", "passed": true, '
            '"_file": "test.py", "_line": 30}\n'
            '[TST] {"type": "block_end", "block": "verdict"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.assertions[0]["_file"] == "test.py"
        assert seg.assertions[0]["_line"] == 30

    def test_segments_error_preserves_source(self):
        """parse_stdout_segments preserves _file/_line on errors."""
        stdout = (
            '[TST] {"type": "block_start", "block": "rigging"}\n'
            '[TST] {"type": "error", "message": "fail", '
            '"_file": "test.py", "_line": 8}\n'
            '[TST] {"type": "block_end", "block": "rigging"}'
        )
        segments = parse_stdout_segments(stdout)
        seg = segments[0]
        assert isinstance(seg, BlockSegment)
        assert seg.errors[0]["_file"] == "test.py"
        assert seg.errors[0]["_line"] == 8


class TestStepParsing:
    """Tests for step parsing within blocks.

    This class contains all tests related to the step feature, starting
    with the backward compatibility baseline test.
    """

    def test_backward_compat_no_steps(self):
        """Block without step events produces identical output to current parser.

        Verifies that a block with features, measurements, results, errors,
        and plain text -- but no step_start/step_end events -- produces a
        BlockSegment with steps == [] and all flat lists populated as before.
        This test anchors backward compatibility for all subsequent step
        parsing changes.
        """
        lines = [
            '[TST] {"type": "block_start", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "payment_gateway"}',
            '[TST] {"type": "feature", "name": "user_accounts"}',
            '[TST] {"type": "block_end", "block": "rigging"}',
            '[TST] {"type": "block_start", "block": "stimulation", '
            '"description": "Place order for 3 items"}',
            "Running order placement...",
            '[TST] {"type": "measurement", "name": "order_total", '
            '"value": 129.97}',
            '[TST] {"type": "measurement", "name": "items_count", '
            '"value": 3}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
            '[TST] {"type": "block_start", "block": "checkpoint"}',
            '[TST] {"type": "result", "status": "pass", '
            '"message": "order placed successfully"}',
            '[TST] {"type": "error", "message": "minor warning"}',
            '[TST] {"type": "block_end", "block": "checkpoint"}',
            '[TST] {"type": "block_start", "block": "verdict"}',
            '[TST] {"type": "result", "status": "pass", '
            '"message": "all checks passed"}',
            '[TST] {"type": "block_end", "block": "verdict"}',
        ]
        result = parse_test_output(lines)

        # Block sequence is correct
        assert result.block_sequence == [
            "rigging", "stimulation", "checkpoint", "verdict",
        ]

        # Rigging block: features populated, steps empty
        assert result.rigging is not None
        assert len(result.rigging.features) == 2
        assert result.rigging.features[0]["name"] == "payment_gateway"
        assert result.rigging.features[1]["name"] == "user_accounts"
        assert result.rigging.steps == []

        # Stimulation block: measurements and plain text, steps empty
        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        assert len(stim) == 1
        assert len(stim[0].measurements) == 2
        assert stim[0].measurements[0]["name"] == "order_total"
        assert stim[0].measurements[1]["name"] == "items_count"
        assert "Running order placement..." in stim[0].logs
        assert stim[0].steps == []

        # Checkpoint block: results and errors, steps empty
        chk = [b for b in result.run_blocks if b.block == "checkpoint"]
        assert len(chk) == 1
        assert len(chk[0].results) == 1
        assert chk[0].results[0]["status"] == "pass"
        assert len(chk[0].errors) == 1
        assert chk[0].errors[0]["message"] == "minor warning"
        assert chk[0].steps == []

        # Verdict block: results, steps empty
        assert result.verdict is not None
        assert len(result.verdict.results) == 1
        assert result.verdict.steps == []

        # No parser warnings
        assert result.warnings == []

    def test_single_step_in_block(self):
        """One step inside a block. Verify tree and bubbled events."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "create_order", '
            '"description": "Create a new order"}',
            '[TST] {"type": "measurement", "name": "order_id", "value": 12345}',
            '[TST] {"type": "step_end", "step": "create_order"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        assert len(stim) == 1
        block = stim[0]

        # Step tree
        assert len(block.steps) == 1
        step = block.steps[0]
        assert step.step == "create_order"
        assert step.description == "Create a new order"
        assert step.status == "passed"
        assert len(step.measurements) == 1
        assert step.measurements[0]["name"] == "order_id"
        assert step.measurements[0]["value"] == 12345

        # Bubbled measurement in block with step-qualified name
        assert len(block.measurements) == 1
        assert block.measurements[0]["name"] == "create_order.order_id"
        assert block.measurements[0]["value"] == 12345

        assert result.warnings == []

    def test_nested_steps(self):
        """Three levels of nesting: block > step A > step B > step C."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "A", "description": "Step A"}',
            '[TST] {"type": "step_start", "step": "B", "description": "Step B"}',
            '[TST] {"type": "step_start", "step": "C", "description": "Step C"}',
            '[TST] {"type": "measurement", "name": "val", "value": 1}',
            '[TST] {"type": "step_end", "step": "C"}',
            '[TST] {"type": "step_end", "step": "B"}',
            '[TST] {"type": "step_end", "step": "A"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        # Tree structure: A -> B -> C
        assert len(block.steps) == 1
        step_a = block.steps[0]
        assert step_a.step == "A"
        assert len(step_a.steps) == 1
        step_b = step_a.steps[0]
        assert step_b.step == "B"
        assert len(step_b.steps) == 1
        step_c = step_b.steps[0]
        assert step_c.step == "C"
        assert step_c.steps == []

        # Measurement in innermost step C
        assert len(step_c.measurements) == 1
        assert step_c.measurements[0]["name"] == "val"

        # Bubbled to block with fully qualified name
        assert len(block.measurements) == 1
        assert block.measurements[0]["name"] == "A.B.C.val"

    def test_sequential_steps(self):
        """Two sibling steps in the same block."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "first", '
            '"description": "First step"}',
            '[TST] {"type": "measurement", "name": "x", "value": 1}',
            '[TST] {"type": "step_end", "step": "first"}',
            '[TST] {"type": "step_start", "step": "second", '
            '"description": "Second step"}',
            '[TST] {"type": "measurement", "name": "y", "value": 2}',
            '[TST] {"type": "step_end", "step": "second"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        assert len(block.steps) == 2
        assert block.steps[0].step == "first"
        assert block.steps[1].step == "second"

        # Both steps have their own measurements
        assert len(block.steps[0].measurements) == 1
        assert block.steps[0].measurements[0]["name"] == "x"
        assert len(block.steps[1].measurements) == 1
        assert block.steps[1].measurements[0]["name"] == "y"

        # Block has both bubbled measurements
        assert len(block.measurements) == 2
        assert block.measurements[0]["name"] == "first.x"
        assert block.measurements[1]["name"] == "second.y"

    def test_measurement_bubbling(self):
        """Measurement in nested step has original name in step, qualified in block."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "outer", '
            '"description": "Outer step"}',
            '[TST] {"type": "step_start", "step": "inner", '
            '"description": "Inner step"}',
            '[TST] {"type": "measurement", "name": "charge_amount", '
            '"value": 99.99}',
            '[TST] {"type": "step_end", "step": "inner"}',
            '[TST] {"type": "step_end", "step": "outer"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        # Original name in innermost step
        inner = block.steps[0].steps[0]
        assert inner.step == "inner"
        assert len(inner.measurements) == 1
        assert inner.measurements[0]["name"] == "charge_amount"
        assert inner.measurements[0]["value"] == 99.99

        # Qualified name in block
        assert len(block.measurements) == 1
        assert block.measurements[0]["name"] == "outer.inner.charge_amount"
        assert block.measurements[0]["value"] == 99.99

    def test_result_bubbling(self):
        """Result in step appears in step .results and block .results."""
        lines = [
            '[TST] {"type": "block_start", "block": "checkpoint"}',
            '[TST] {"type": "step_start", "step": "verify", '
            '"description": "Verify result"}',
            '[TST] {"type": "result", "status": "pass", '
            '"message": "order_placed"}',
            '[TST] {"type": "step_end", "step": "verify"}',
            '[TST] {"type": "block_end", "block": "checkpoint"}',
        ]
        result = parse_test_output(lines)

        chk = [b for b in result.run_blocks if b.block == "checkpoint"]
        block = chk[0]

        # Result in step
        step = block.steps[0]
        assert len(step.results) == 1
        assert step.results[0]["status"] == "pass"
        assert step.results[0]["message"] == "order_placed"

        # Result bubbled to block with qualified name
        assert len(block.results) == 1
        assert block.results[0]["status"] == "pass"
        assert block.results[0]["message"] == "verify.order_placed"

    def test_feature_bubbling(self):
        """Feature in step appears in step .features and block .features."""
        lines = [
            '[TST] {"type": "block_start", "block": "rigging"}',
            '[TST] {"type": "step_start", "step": "setup_db", '
            '"description": "Initialize database"}',
            '[TST] {"type": "feature", "name": "auth_service"}',
            '[TST] {"type": "step_end", "step": "setup_db"}',
            '[TST] {"type": "block_end", "block": "rigging"}',
        ]
        result = parse_test_output(lines)

        assert result.rigging is not None
        block = result.rigging

        # Feature in step
        step = block.steps[0]
        assert len(step.features) == 1
        assert step.features[0]["name"] == "auth_service"

        # Feature bubbled to block with qualified name
        assert len(block.features) == 1
        assert block.features[0]["name"] == "setup_db.auth_service"

    def test_error_attribution(self):
        """Error in inner step: error dict in innermost step and block,
        status 'failed' on all ancestors."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "outer", '
            '"description": "Outer"}',
            '[TST] {"type": "step_start", "step": "inner", '
            '"description": "Inner"}',
            '[TST] {"type": "error", "message": "connection refused"}',
            '[TST] {"type": "step_end", "step": "inner"}',
            '[TST] {"type": "step_end", "step": "outer"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        outer = block.steps[0]
        inner = outer.steps[0]

        # Error dict in innermost step
        assert len(inner.errors) == 1
        assert inner.errors[0]["message"] == "connection refused"

        # Error dict NOT in parent step's errors
        assert len(outer.errors) == 0

        # Error dict in block
        assert len(block.errors) == 1
        assert block.errors[0]["message"] == "connection refused"

        # Status propagation: both steps are "failed"
        assert inner.status == "failed"
        assert outer.status == "failed"

    def test_plain_text_attribution(self):
        """Plain text in step goes to step .logs only, not block .logs."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            "Block-level text before step",
            '[TST] {"type": "step_start", "step": "run", '
            '"description": "Run test"}',
            "Step-level log line 1",
            "Step-level log line 2",
            '[TST] {"type": "step_end", "step": "run"}',
            "Block-level text after step",
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        step = block.steps[0]

        # Plain text in step's logs
        assert "Step-level log line 1" in step.logs
        assert "Step-level log line 2" in step.logs

        # Plain text NOT in block's logs
        assert "Step-level log line 1" not in block.logs
        assert "Step-level log line 2" not in block.logs

        # Block-level text IS in block's logs
        assert "Block-level text before step" in block.logs
        assert "Block-level text after step" in block.logs

    def test_events_outside_steps(self):
        """Events inside a block but outside any step go directly to block."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "measurement", "name": "before_step", "value": 1}',
            '[TST] {"type": "step_start", "step": "s1", '
            '"description": "A step"}',
            '[TST] {"type": "measurement", "name": "in_step", "value": 2}',
            '[TST] {"type": "step_end", "step": "s1"}',
            '[TST] {"type": "measurement", "name": "after_step", "value": 3}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        # Block measurements: before_step, s1.in_step (bubbled), after_step
        names = [m["name"] for m in block.measurements]
        assert "before_step" in names
        assert "s1.in_step" in names
        assert "after_step" in names

        # Step measurement: original name
        step = block.steps[0]
        assert len(step.measurements) == 1
        assert step.measurements[0]["name"] == "in_step"

    def test_step_qualified_name_format(self):
        """Deep nesting (3 levels) produces qualified name a.b.c.name."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "a", "description": "A"}',
            '[TST] {"type": "step_start", "step": "b", "description": "B"}',
            '[TST] {"type": "step_start", "step": "c", "description": "C"}',
            '[TST] {"type": "measurement", "name": "metric", "value": 42}',
            '[TST] {"type": "step_end", "step": "c"}',
            '[TST] {"type": "step_end", "step": "b"}',
            '[TST] {"type": "step_end", "step": "a"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        assert len(block.measurements) == 1
        assert block.measurements[0]["name"] == "a.b.c.metric"

    # --- Error recovery tests (Step 2.1) ---

    def test_step_end_name_mismatch(self):
        """step_end with wrong name: valid prefix kept, remainder in undefined step."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "A", "description": "Step A"}',
            '[TST] {"type": "step_start", "step": "B", "description": "Step B"}',
            '[TST] {"type": "measurement", "name": "m1", "value": 1}',
            # Mismatch: innermost step is B but we end A
            '[TST] {"type": "step_end", "step": "A"}',
            '[TST] {"type": "measurement", "name": "m2", "value": 2}',
            '[TST] {"type": "step_end", "step": "A"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        # Step A is the top-level step
        assert len(block.steps) == 1
        step_a = block.steps[0]
        assert step_a.step == "A"

        # Step B is under A (valid prefix)
        assert len(step_a.steps) >= 1
        step_b = step_a.steps[0]
        assert step_b.step == "B"
        assert len(step_b.measurements) == 1
        assert step_b.measurements[0]["name"] == "m1"

        # There should be an undefined step for the remainder
        undef_steps = [
            s for s in step_a.steps if s.step == "undefined"
        ]
        assert len(undef_steps) >= 1
        undef = undef_steps[0]
        assert undef.status == "warning"

        # Parser warning recorded
        assert any("mismatch" in w for w in result.warnings)

    def test_step_start_outside_block(self):
        """step_start before any block_start creates undefined block."""
        lines = [
            '[TST] {"type": "step_start", "step": "orphan", '
            '"description": "Orphan step"}',
            '[TST] {"type": "measurement", "name": "val", "value": 1}',
            '[TST] {"type": "step_end", "step": "orphan"}',
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        # Should have an undefined block in run_blocks
        undef_blocks = [
            b for b in result.run_blocks if b.block == "undefined"
        ]
        assert len(undef_blocks) >= 1

        # The undefined block should contain the orphan step
        undef_block = undef_blocks[0]
        assert len(undef_block.steps) >= 1
        assert undef_block.steps[0].step == "orphan"

        # Parser warning recorded
        assert any("outside" in w for w in result.warnings)

        # The stimulation block should still exist
        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        assert len(stim) == 1

    def test_block_end_unclosed_steps(self):
        """block_end while steps still open: unclosed steps get warning status."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "first", '
            '"description": "First step"}',
            '[TST] {"type": "measurement", "name": "m1", "value": 1}',
            '[TST] {"type": "step_end", "step": "first"}',
            '[TST] {"type": "step_start", "step": "second", '
            '"description": "Second step (never closed)"}',
            '[TST] {"type": "measurement", "name": "m2", "value": 2}',
            # No step_end for "second" -- block ends
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        # First step is properly closed (valid prefix)
        assert len(block.steps) >= 2
        first = block.steps[0]
        assert first.step == "first"
        assert first.status == "passed"

        # Second step was never closed -- should have warning status
        second = block.steps[1]
        assert second.step == "second"
        assert second.status == "warning"

        # Block still contains bubbled measurements from both steps
        names = [m["name"] for m in block.measurements]
        assert "first.m1" in names
        assert "second.m2" in names

        # Parser warning about unclosed step
        assert any("never closed" in w for w in result.warnings)

    def test_duplicate_step_names(self):
        """Duplicate step name in same scope: first kept, remainder in undefined."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "action", '
            '"description": "First action"}',
            '[TST] {"type": "measurement", "name": "m1", "value": 1}',
            '[TST] {"type": "step_end", "step": "action"}',
            # Duplicate name "action" in same scope (block level)
            '[TST] {"type": "step_start", "step": "action", '
            '"description": "Duplicate action"}',
            '[TST] {"type": "measurement", "name": "m2", "value": 2}',
            '[TST] {"type": "step_end", "step": "action"}',
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        # First step "action" is kept as valid prefix
        assert block.steps[0].step == "action"
        assert len(block.steps[0].measurements) == 1
        assert block.steps[0].measurements[0]["name"] == "m1"

        # There should be an undefined step for the remainder
        undef_steps = [
            s for s in block.steps if s.step == "undefined"
        ]
        assert len(undef_steps) >= 1
        undef = undef_steps[0]
        assert undef.status == "warning"

        # Parser warning about duplicate
        assert any("duplicate" in w for w in result.warnings)

    def test_failed_takes_precedence_over_warning(self):
        """Step with both error event and structural issue: status is 'failed'."""
        lines = [
            '[TST] {"type": "block_start", "block": "stimulation"}',
            '[TST] {"type": "step_start", "step": "broken", '
            '"description": "A broken step"}',
            '[TST] {"type": "error", "message": "something broke"}',
            # Step is never closed -- structural error
            '[TST] {"type": "block_end", "block": "stimulation"}',
        ]
        result = parse_test_output(lines)

        stim = [b for b in result.run_blocks if b.block == "stimulation"]
        block = stim[0]

        step = block.steps[0]
        assert step.step == "broken"
        # "failed" takes precedence over "warning"
        assert step.status == "failed"

        # Both warnings exist
        assert any("never closed" in w for w in result.warnings)
        # Error is recorded
        assert len(step.errors) == 1
        assert step.errors[0]["message"] == "something broke"
