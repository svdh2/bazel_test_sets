"""Unit tests for the status file module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orchestrator.status import DEFAULT_CONFIG, VALID_STATES, StatusFile


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
            assert "config" in data
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
            sf1.set_test_state("//test:a", "stable", runs=50, passes=50)
            sf1.set_test_state("//test:b", "burning_in", runs=12, passes=12)
            sf1.save()

            sf2 = StatusFile(path)
            assert sf2.get_test_state("//test:a") == "stable"
            assert sf2.get_test_state("//test:b") == "burning_in"

            entry_a = sf2.get_test_entry("//test:a")
            assert entry_a is not None
            assert entry_a["runs"] == 50
            assert entry_a["passes"] == 50

    def test_get_nonexistent_test(self):
        """Getting state of nonexistent test returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.get_test_state("//test:nonexistent") is None
            assert sf.get_test_entry("//test:nonexistent") is None

    def test_update_existing_test(self):
        """Updating existing test preserves runs if not specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in", runs=10, passes=10)
            sf.set_test_state("//test:a", "stable")

            entry = sf.get_test_entry("//test:a")
            assert entry is not None
            assert entry["state"] == "stable"
            assert entry["runs"] == 10
            assert entry["passes"] == 10


class TestStatusFileConfig:
    """Tests for configuration management."""

    def test_default_config(self):
        """Default config matches expected values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.min_reliability == 0.99
            assert sf.statistical_significance == 0.95

    def test_set_config(self):
        """Config can be updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_config(min_reliability=0.95, statistical_significance=0.99)
            assert sf.min_reliability == 0.95
            assert sf.statistical_significance == 0.99

    def test_config_roundtrip(self):
        """Config survives save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf1 = StatusFile(path)
            sf1.set_config(min_reliability=0.95)
            sf1.save()

            sf2 = StatusFile(path)
            assert sf2.min_reliability == 0.95

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
            assert entry["runs"] == 1
            assert entry["passes"] == 1

    def test_record_run_existing_test(self):
        """Recording runs increments counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in", runs=5, passes=5)

            sf.record_run("//test:a", passed=True)
            entry = sf.get_test_entry("//test:a")
            assert entry is not None
            assert entry["runs"] == 6
            assert entry["passes"] == 6

            sf.record_run("//test:a", passed=False)
            entry = sf.get_test_entry("//test:a")
            assert entry is not None
            assert entry["runs"] == 7
            assert entry["passes"] == 6

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
            sf.set_test_state("//test:a", "stable", runs=50, passes=50)
            sf.set_test_state("//test:b", "burning_in", runs=10, passes=10)
            sf.set_test_state("//test:c", "stable", runs=30, passes=30)
            sf.set_test_state("//test:d", "flaky", runs=20, passes=15)

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
        assert VALID_STATES == {"new", "burning_in", "stable", "flaky"}


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
