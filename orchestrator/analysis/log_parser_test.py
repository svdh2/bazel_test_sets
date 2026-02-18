"""Tests for the structured test log parser."""

from __future__ import annotations

import pytest

from orchestrator.analysis.log_parser import (
    SENTINEL,
    BlockSegment,
    ParsedOutput,
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
