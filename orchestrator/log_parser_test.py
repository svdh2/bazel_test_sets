"""Tests for the structured test log parser."""

from __future__ import annotations

import pytest

from orchestrator.log_parser import (
    SENTINEL,
    parse_test_output,
    is_rigging_failure,
    get_rigging_features,
)


class TestPhaseEvents:
    """Tests for phase event parsing."""

    def test_single_phase(self):
        """Parse a single phase event."""
        lines = ['[TST] {"type": "phase", "block": "rigging"}']
        result = parse_test_output(lines)
        assert result["block_sequence"] == ["rigging"]

    def test_multiple_phases(self):
        """Parse multiple phase events in order."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "phase", "block": "stimulation"}',
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "phase", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        assert result["block_sequence"] == [
            "rigging",
            "stimulation",
            "checkpoint",
            "verdict",
        ]

    def test_phase_without_block(self):
        """Phase event without block field is ignored."""
        lines = ['[TST] {"type": "phase"}']
        result = parse_test_output(lines)
        assert result["block_sequence"] == []

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
        assert result["block_sequence"] == ["rigging", "stimulation"]
        assert "Setting up test environment..." in result["plain_output"]
        assert "Rigging complete" in result["plain_output"]
        assert "Running test..." in result["plain_output"]


class TestFeatureEvents:
    """Tests for feature event parsing."""

    def test_feature_in_rigging(self):
        """Feature event during rigging phase is captured with block."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "user_auth"}',
        ]
        result = parse_test_output(lines)
        assert len(result["features"]) == 1
        assert result["features"][0]["name"] == "user_auth"
        assert result["features"][0]["block"] == "rigging"

    def test_feature_outside_rigging(self):
        """Feature event outside rigging is still captured with its block."""
        lines = [
            '[TST] {"type": "phase", "block": "stimulation"}',
            '[TST] {"type": "feature", "name": "some_feature"}',
        ]
        result = parse_test_output(lines)
        assert len(result["features"]) == 1
        assert result["features"][0]["block"] == "stimulation"

    def test_feature_before_any_phase(self):
        """Feature event before any phase has block=None."""
        lines = ['[TST] {"type": "feature", "name": "early_feature"}']
        result = parse_test_output(lines)
        assert len(result["features"]) == 1
        assert result["features"][0]["block"] is None

    def test_feature_without_name(self):
        """Feature event without name defaults to empty string."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature"}',
        ]
        result = parse_test_output(lines)
        assert result["features"][0]["name"] == ""

    def test_multiple_features(self):
        """Multiple feature events are all captured."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "feature", "name": "auth"}',
            '[TST] {"type": "feature", "name": "billing"}',
            '[TST] {"type": "feature", "name": "notifications"}',
        ]
        result = parse_test_output(lines)
        assert len(result["features"]) == 3
        names = [f["name"] for f in result["features"]]
        assert names == ["auth", "billing", "notifications"]


class TestMeasurementEvents:
    """Tests for measurement event parsing."""

    def test_simple_measurement(self):
        """Parse a simple measurement with name and value."""
        lines = [
            '[TST] {"type": "measurement", "name": "response_time", "value": 142.0}'
        ]
        result = parse_test_output(lines)
        assert len(result["measurements"]) == 1
        assert result["measurements"][0]["name"] == "response_time"
        assert result["measurements"][0]["value"] == 142.0

    def test_measurement_with_structured_value(self):
        """Parse a measurement with a structured (dict) value."""
        lines = [
            '[TST] {"type": "measurement", "name": "response_time", '
            '"value": {"value": 142.0, "unit": "ms"}}'
        ]
        result = parse_test_output(lines)
        assert result["measurements"][0]["value"] == {
            "value": 142.0,
            "unit": "ms",
        }

    def test_measurement_without_value(self):
        """Measurement without value field has value=None."""
        lines = ['[TST] {"type": "measurement", "name": "counter"}']
        result = parse_test_output(lines)
        assert result["measurements"][0]["value"] is None

    def test_measurement_tracks_block(self):
        """Measurement records the current block."""
        lines = [
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "measurement", "name": "latency", "value": 50}',
        ]
        result = parse_test_output(lines)
        assert result["measurements"][0]["block"] == "checkpoint"


class TestResultEvents:
    """Tests for result event parsing."""

    def test_pass_result(self):
        """Parse a passing result event."""
        lines = [
            '[TST] {"type": "phase", "block": "verdict"}',
            '[TST] {"type": "result", "status": "pass", "message": "all checks passed"}',
        ]
        result = parse_test_output(lines)
        assert len(result["results"]) == 1
        assert result["results"][0]["status"] == "pass"
        assert result["results"][0]["message"] == "all checks passed"
        assert result["results"][0]["block"] == "verdict"

    def test_fail_result(self):
        """Parse a failing result event."""
        lines = [
            '[TST] {"type": "result", "status": "fail", "message": "assertion failed"}'
        ]
        result = parse_test_output(lines)
        assert result["results"][0]["status"] == "fail"

    def test_result_without_fields(self):
        """Result without status/message defaults to empty strings."""
        lines = ['[TST] {"type": "result"}']
        result = parse_test_output(lines)
        assert result["results"][0]["status"] == ""
        assert result["results"][0]["message"] == ""


class TestErrorEvents:
    """Tests for error event parsing."""

    def test_error_in_rigging(self):
        """Error during rigging phase is tagged with rigging block."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "error", "message": "cannot connect to database"}',
        ]
        result = parse_test_output(lines)
        assert len(result["errors"]) == 1
        assert result["errors"][0]["message"] == "cannot connect to database"
        assert result["errors"][0]["block"] == "rigging"

    def test_error_without_message(self):
        """Error without message defaults to empty string."""
        lines = ['[TST] {"type": "error"}']
        result = parse_test_output(lines)
        assert result["errors"][0]["message"] == ""

    def test_error_outside_block(self):
        """Error before any phase has block=None."""
        lines = ['[TST] {"type": "error", "message": "startup failure"}']
        result = parse_test_output(lines)
        assert result["errors"][0]["block"] is None


class TestMalformedInput:
    """Tests for malformed input handling."""

    def test_invalid_json(self):
        """Malformed JSON after sentinel produces a warning."""
        lines = ["[TST] not json at all"]
        result = parse_test_output(lines)
        assert len(result["warnings"]) == 1
        assert "malformed" in result["warnings"][0]
        assert result["block_sequence"] == []

    def test_non_object_json(self):
        """JSON that is not an object produces a warning."""
        lines = ['[TST] [1, 2, 3]']
        result = parse_test_output(lines)
        assert len(result["warnings"]) == 1
        assert "not a JSON object" in result["warnings"][0]

    def test_missing_type_field(self):
        """JSON object without type field produces a warning."""
        lines = ['[TST] {"name": "something"}']
        result = parse_test_output(lines)
        assert len(result["warnings"]) == 1
        assert "missing type" in result["warnings"][0]

    def test_malformed_does_not_affect_valid_lines(self):
        """Valid lines after malformed lines are still parsed correctly."""
        lines = [
            "[TST] not json",
            '[TST] {"type": "phase", "block": "verdict"}',
            '[TST] {"no_type": true}',
            '[TST] {"type": "result", "status": "pass", "message": "ok"}',
        ]
        result = parse_test_output(lines)
        assert result["block_sequence"] == ["verdict"]
        assert len(result["results"]) == 1
        assert result["results"][0]["status"] == "pass"
        assert len(result["warnings"]) == 2

    def test_empty_sentinel_line(self):
        """Sentinel prefix with empty content produces a warning."""
        lines = ["[TST] "]
        result = parse_test_output(lines)
        assert len(result["warnings"]) == 1


class TestUnknownTypes:
    """Tests for unknown type handling (forward compatibility)."""

    def test_unknown_type_skipped(self):
        """Unknown event type is silently skipped."""
        lines = ['[TST] {"type": "future_event", "data": "something"}']
        result = parse_test_output(lines)
        assert result["block_sequence"] == []
        assert result["features"] == []
        assert result["measurements"] == []
        assert result["results"] == []
        assert result["errors"] == []
        assert result["warnings"] == []

    def test_unknown_type_does_not_break_state(self):
        """Unknown type does not affect current_block tracking."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "future_event", "data": "x"}',
            '[TST] {"type": "feature", "name": "auth"}',
        ]
        result = parse_test_output(lines)
        assert result["features"][0]["block"] == "rigging"


class TestPlainOutput:
    """Tests for non-sentinel line collection."""

    def test_plain_lines_collected(self):
        """Lines without sentinel are collected as plain output."""
        lines = [
            "Hello world",
            "Test running...",
            '[TST] {"type": "phase", "block": "rigging"}',
            "More output",
        ]
        result = parse_test_output(lines)
        assert result["plain_output"] == [
            "Hello world",
            "Test running...",
            "More output",
        ]

    def test_no_sentinel_lines(self):
        """All lines without sentinel results in empty parsed events."""
        lines = ["line 1", "line 2"]
        result = parse_test_output(lines)
        assert result["block_sequence"] == []
        assert result["features"] == []
        assert result["plain_output"] == ["line 1", "line 2"]

    def test_empty_input(self):
        """Empty input produces empty result."""
        result = parse_test_output([])
        assert result["block_sequence"] == []
        assert result["features"] == []
        assert result["measurements"] == []
        assert result["results"] == []
        assert result["errors"] == []
        assert result["plain_output"] == []
        assert result["warnings"] == []


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
        assert result["block_sequence"] == ["rigging", "verdict"]
        assert "some output" in result["plain_output"]

    def test_empty_string(self):
        """Empty string produces empty result."""
        result = parse_test_output("")
        assert result["block_sequence"] == []
        # splitlines on "" gives [] so no plain_output either
        assert result["plain_output"] == []


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
        assert result["block_sequence"] == [
            "rigging",
            "stimulation",
            "checkpoint",
            "verdict",
        ]

        # Verify features (captured with rigging block)
        assert len(result["features"]) == 2
        assert result["features"][0] == {
            "name": "payment_gateway",
            "block": "rigging",
        }
        assert result["features"][1] == {
            "name": "user_accounts",
            "block": "rigging",
        }

        # Verify measurements (in checkpoint block)
        assert len(result["measurements"]) == 2
        assert result["measurements"][0]["name"] == "response_time"
        assert result["measurements"][0]["value"] == {
            "value": 142.0,
            "unit": "ms",
        }
        assert result["measurements"][0]["block"] == "checkpoint"
        assert result["measurements"][1]["name"] == "status_code"
        assert result["measurements"][1]["value"] == 200

        # Verify results
        assert len(result["results"]) == 2
        assert result["results"][0]["block"] == "checkpoint"
        assert result["results"][1]["block"] == "verdict"

        # Verify plain output
        assert "=== Test: //tests:payment_flow ===" in result["plain_output"]
        assert "Setting up payment service..." in result["plain_output"]
        assert "Test complete." in result["plain_output"]

        # No errors or warnings
        assert result["errors"] == []
        assert result["warnings"] == []


class TestIsRiggingFailure:
    """Tests for is_rigging_failure helper."""

    def test_rigging_error_is_failure(self):
        """Error during rigging phase is a rigging failure."""
        parsed = {
            "errors": [{"message": "db down", "block": "rigging"}],
        }
        assert is_rigging_failure(parsed) is True

    def test_non_rigging_error_not_failure(self):
        """Error during non-rigging phase is not a rigging failure."""
        parsed = {
            "errors": [{"message": "assertion failed", "block": "checkpoint"}],
        }
        assert is_rigging_failure(parsed) is False

    def test_no_errors_not_failure(self):
        """No errors means not a rigging failure."""
        parsed = {"errors": []}
        assert is_rigging_failure(parsed) is False

    def test_missing_errors_key(self):
        """Missing errors key means not a rigging failure."""
        parsed = {}
        assert is_rigging_failure(parsed) is False

    def test_mixed_errors(self):
        """One rigging error among others is still a rigging failure."""
        parsed = {
            "errors": [
                {"message": "test error", "block": "checkpoint"},
                {"message": "rigging error", "block": "rigging"},
            ],
        }
        assert is_rigging_failure(parsed) is True


class TestGetRiggingFeatures:
    """Tests for get_rigging_features helper."""

    def test_features_in_rigging(self):
        """Extracts feature names from rigging phase."""
        parsed = {
            "features": [
                {"name": "auth", "block": "rigging"},
                {"name": "billing", "block": "rigging"},
            ],
        }
        assert get_rigging_features(parsed) == ["auth", "billing"]

    def test_features_not_in_rigging(self):
        """Features not in rigging are excluded."""
        parsed = {
            "features": [
                {"name": "auth", "block": "rigging"},
                {"name": "perf", "block": "stimulation"},
            ],
        }
        assert get_rigging_features(parsed) == ["auth"]

    def test_no_features(self):
        """No features returns empty list."""
        parsed = {"features": []}
        assert get_rigging_features(parsed) == []

    def test_missing_features_key(self):
        """Missing features key returns empty list."""
        parsed = {}
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
        assert result["has_rigging_failure"] is True

    def test_no_error_flag_false(self):
        """No errors means has_rigging_failure=False."""
        lines = [
            '[TST] {"type": "phase", "block": "rigging"}',
            '[TST] {"type": "phase", "block": "verdict"}',
        ]
        result = parse_test_output(lines)
        assert result["has_rigging_failure"] is False

    def test_non_rigging_error_flag_false(self):
        """Error outside rigging phase has has_rigging_failure=False."""
        lines = [
            '[TST] {"type": "phase", "block": "checkpoint"}',
            '[TST] {"type": "error", "message": "test error"}',
        ]
        result = parse_test_output(lines)
        assert result["has_rigging_failure"] is False

    def test_empty_input_flag_false(self):
        """Empty input has has_rigging_failure=False."""
        result = parse_test_output([])
        assert result["has_rigging_failure"] is False
