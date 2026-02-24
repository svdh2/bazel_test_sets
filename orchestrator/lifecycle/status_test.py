"""Unit tests for the status file module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orchestrator.lifecycle.status import (
    DEFAULT_CONFIG,
    HISTORY_CAP,
    VALID_STATES,
    StatusFile,
    runs_and_passes_from_history,
)


class TestStatusFileCreate:
    """Tests for creating new status files."""

    def test_create_new_file(self):
        """StatusFile creates empty state for nonexistent file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf = StatusFile(path)

            assert sf.get_all_tests() == {}
            assert sf.min_reliability == DEFAULT_CONFIG["min_reliability"]
            assert (
                sf.statistical_significance
                == DEFAULT_CONFIG["statistical_significance"]
            )

    def test_save_creates_file(self):
        """save() creates the file on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf = StatusFile(path)
            sf.save()

            assert path.exists()
            data = json.loads(path.read_text())
            assert "config" not in data
            assert "tests" in data

    def test_save_creates_parent_dirs(self):
        """save() creates parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "dir" / "status.json"
            sf = StatusFile(path)
            sf.save()
            assert path.exists()


class TestStatusFileReadWrite:
    """Tests for reading and writing state."""

    def test_roundtrip(self):
        """State survives save/load roundtrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"

            sf1 = StatusFile(path)
            sf1.set_test_state("//test:a", "stable")
            for _ in range(50):
                sf1.record_run("//test:a", passed=True)
            sf1.set_test_state("//test:b", "burning_in")
            for _ in range(12):
                sf1.record_run("//test:b", passed=True)
            sf1.save()

            sf2 = StatusFile(path)
            assert sf2.get_test_state("//test:a") == "stable"
            assert sf2.get_test_state("//test:b") == "burning_in"

            runs, passes = runs_and_passes_from_history(
                sf2.get_test_history("//test:a")
            )
            assert runs == 50
            assert passes == 50

    def test_get_nonexistent_test(self):
        """Getting state of nonexistent test returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.get_test_state("//test:nonexistent") is None
            assert sf.get_test_entry("//test:nonexistent") is None

    def test_update_existing_test(self):
        """Updating existing test preserves history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            for _ in range(10):
                sf.record_run("//test:a", passed=True)
            sf.set_test_state("//test:a", "stable")

            entry = sf.get_test_entry("//test:a")
            assert entry is not None
            assert entry["state"] == "stable"
            runs, passes = runs_and_passes_from_history(
                sf.get_test_history("//test:a")
            )
            assert runs == 10
            assert passes == 10


class TestStatusFileConfig:
    """Tests for statistical parameter configuration."""

    def test_default_config(self):
        """Default config matches expected values when no params passed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.min_reliability == 0.99
            assert sf.statistical_significance == 0.95

    def test_explicit_params(self):
        """Statistical params can be passed directly to constructor."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(
                Path(tmpdir) / "status.json",
                min_reliability=0.95,
                statistical_significance=0.90,
            )
            assert sf.min_reliability == 0.95
            assert sf.statistical_significance == 0.90

    def test_set_config(self):
        """Config can be updated in memory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_config(min_reliability=0.95, statistical_significance=0.99)
            assert sf.min_reliability == 0.95
            assert sf.statistical_significance == 0.99

    def test_partial_config_update(self):
        """Updating one config value doesn't affect others."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_config(min_reliability=0.90)
            assert sf.statistical_significance == 0.95  # unchanged


class TestStatusFileRecordRun:
    """Tests for recording test runs."""

    def test_record_run_new_test(self):
        """Recording a run for a new test creates it with state 'new'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", passed=True)

            entry = sf.get_test_entry("//test:a")
            assert entry is not None
            assert entry["state"] == "new"
            runs, passes = runs_and_passes_from_history(
                sf.get_test_history("//test:a")
            )
            assert runs == 1
            assert passes == 1

    def test_record_run_existing_test(self):
        """Recording runs grows history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            for _ in range(5):
                sf.record_run("//test:a", passed=True)

            sf.record_run("//test:a", passed=True)
            runs, passes = runs_and_passes_from_history(
                sf.get_test_history("//test:a")
            )
            assert runs == 6
            assert passes == 6

            sf.record_run("//test:a", passed=False)
            runs, passes = runs_and_passes_from_history(
                sf.get_test_history("//test:a")
            )
            assert runs == 7
            assert passes == 6

    def test_record_run_updates_timestamp(self):
        """Recording a run updates last_updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", passed=True)
            entry = sf.get_test_entry("//test:a")
            assert entry is not None
            assert "last_updated" in entry


class TestStatusFileQuery:
    """Tests for querying tests by state."""

    def test_get_tests_by_state(self):
        """Filter tests by state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "burning_in")
            sf.set_test_state("//test:c", "stable")
            sf.set_test_state("//test:d", "flaky")

            stable = sf.get_tests_by_state("stable")
            assert sorted(stable) == ["//test:a", "//test:c"]

            burning = sf.get_tests_by_state("burning_in")
            assert burning == ["//test:b"]

            flaky = sf.get_tests_by_state("flaky")
            assert flaky == ["//test:d"]

    def test_get_tests_by_state_empty(self):
        """No tests with given state returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.get_tests_by_state("stable") == []

    def test_get_all_tests(self):
        """Get all test entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "flaky")

            all_tests = sf.get_all_tests()
            assert len(all_tests) == 2
            assert "//test:a" in all_tests
            assert "//test:b" in all_tests


class TestStatusFileRemove:
    """Tests for removing tests."""

    def test_remove_existing(self):
        """Remove an existing test."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            assert sf.remove_test("//test:a") is True
            assert sf.get_test_state("//test:a") is None

    def test_remove_nonexistent(self):
        """Removing nonexistent test returns False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.remove_test("//test:nonexistent") is False


class TestStatusFileValidation:
    """Tests for input validation."""

    def test_invalid_state_raises(self):
        """Setting an invalid state raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            with pytest.raises(ValueError, match="Invalid state"):
                sf.set_test_state("//test:a", "invalid_state")

    def test_valid_states_constant(self):
        """VALID_STATES contains expected values."""
        assert VALID_STATES == {"new", "burning_in", "stable", "flaky", "disabled"}


class TestStatusFileCorrupted:
    """Tests for handling corrupted files."""

    def test_corrupted_json(self):
        """Corrupted JSON file starts fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            path.write_text("{ invalid json }")
            sf = StatusFile(path)
            assert sf.get_all_tests() == {}

    def test_empty_file(self):
        """Empty file starts fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            path.write_text("")
            sf = StatusFile(path)
            assert sf.get_all_tests() == {}

    def test_missing_sections(self):
        """File with missing sections gets defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            path.write_text('{"some_key": "value"}')
            sf = StatusFile(path)
            assert sf.min_reliability == DEFAULT_CONFIG["min_reliability"]
            assert sf.get_all_tests() == {}


class TestStatusFileHistory:
    """Tests for per-run history tracking."""

    def test_record_run_creates_history_entry(self):
        """record_run creates a history entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", passed=True, commit="abc123")

            history = sf.get_test_history("//test:a")
            assert len(history) == 1
            assert history[0] == {"passed": True, "commit": "abc123"}

    def test_history_newest_first(self):
        """History is stored newest-first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", passed=True, commit="aaa")
            sf.record_run("//test:a", passed=False, commit="bbb")
            sf.record_run("//test:a", passed=True, commit="ccc")

            history = sf.get_test_history("//test:a")
            assert len(history) == 3
            assert history[0] == {"passed": True, "commit": "ccc"}
            assert history[1] == {"passed": False, "commit": "bbb"}
            assert history[2] == {"passed": True, "commit": "aaa"}

    def test_history_without_commit(self):
        """record_run without commit stores None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", passed=True)

            history = sf.get_test_history("//test:a")
            assert history[0] == {"passed": True, "commit": None}

    def test_history_capped_at_limit(self):
        """History is capped at HISTORY_CAP entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            for i in range(HISTORY_CAP + 10):
                sf.record_run("//test:a", passed=True, commit=f"c{i}")

            history = sf.get_test_history("//test:a")
            assert len(history) == HISTORY_CAP
            # Newest entry should be the last one recorded
            assert history[0]["commit"] == f"c{HISTORY_CAP + 9}"

    def test_history_survives_roundtrip(self):
        """History persists through save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf1 = StatusFile(path)
            sf1.record_run("//test:a", passed=True, commit="abc")
            sf1.record_run("//test:a", passed=False, commit="def")
            sf1.save()

            sf2 = StatusFile(path)
            history = sf2.get_test_history("//test:a")
            assert len(history) == 2
            assert history[0] == {"passed": False, "commit": "def"}
            assert history[1] == {"passed": True, "commit": "abc"}

    def test_backward_compat_missing_history_field(self):
        """Old status files without history field return empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            # Write a status file in the old format (no history)
            data = {
                "tests": {
                    "//test:a": {
                        "state": "stable",
                        "runs": 50,
                        "passes": 50,
                        "last_updated": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
            path.write_text(json.dumps(data))

            sf = StatusFile(path)
            assert sf.get_test_history("//test:a") == []

            # After recording a run, history should start populating
            sf.record_run("//test:a", passed=True, commit="abc")
            assert len(sf.get_test_history("//test:a")) == 1

    def test_reset_clears_history(self):
        """set_test_state with clear_history=True clears history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.record_run("//test:a", passed=True, commit="abc")
            sf.record_run("//test:a", passed=False, commit="def")
            assert len(sf.get_test_history("//test:a")) == 2

            # Reset (deflake scenario)
            sf.set_test_state("//test:a", "burning_in", clear_history=True)
            assert sf.get_test_history("//test:a") == []

    def test_set_test_state_preserves_history(self):
        """set_test_state without clear_history preserves history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in", clear_history=True)
            sf.record_run("//test:a", passed=True, commit="abc")
            sf.record_run("//test:a", passed=True, commit="def")

            # Transition to stable (preserving history)
            sf.set_test_state("//test:a", "stable")
            assert len(sf.get_test_history("//test:a")) == 2

    def test_get_test_history_nonexistent(self):
        """get_test_history for unknown test returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.get_test_history("//test:nonexistent") == []

    def test_get_test_history_returns_copy(self):
        """get_test_history returns a copy, not a reference."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", passed=True, commit="abc")
            history = sf.get_test_history("//test:a")
            history.clear()
            assert len(sf.get_test_history("//test:a")) == 1


class TestStatusFileDisabled:
    """Tests for the disabled state."""

    def test_disabled_state_roundtrip(self):
        """Disabled state survives save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf1 = StatusFile(path)
            sf1.set_test_state("//test:a", "disabled", clear_history=True)
            sf1.save()

            sf2 = StatusFile(path)
            assert sf2.get_test_state("//test:a") == "disabled"

    def test_get_tests_by_state_disabled(self):
        """Filter tests by disabled state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "disabled", clear_history=True)
            sf.set_test_state("//test:c", "disabled", clear_history=True)

            disabled = sf.get_tests_by_state("disabled")
            assert sorted(disabled) == ["//test:b", "//test:c"]

    def test_disabled_resets_history(self):
        """Setting state to disabled with clear_history clears history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.record_run("//test:a", passed=True, commit="abc")
            assert len(sf.get_test_history("//test:a")) == 1

            sf.set_test_state("//test:a", "disabled", clear_history=True)
            assert sf.get_test_history("//test:a") == []
