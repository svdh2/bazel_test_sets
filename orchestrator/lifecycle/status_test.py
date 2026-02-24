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


class TestStatusFileTargetHash:
    """Tests for target hash storage and retrieval."""

    def test_set_and_get_target_hash(self):
        """Target hash can be stored and retrieved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash123")
            assert sf.get_target_hash("//test:a") == "hash123"

    def test_get_target_hash_nonexistent_test(self):
        """Getting hash for nonexistent test returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.get_target_hash("//test:nonexistent") is None

    def test_get_target_hash_no_hash_stored(self):
        """Getting hash for test without hash returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            assert sf.get_target_hash("//test:a") is None

    def test_set_target_hash_creates_test_entry(self):
        """Setting hash for nonexistent test creates it with state 'new'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_target_hash("//test:a", "hash123")
            assert sf.get_test_state("//test:a") == "new"
            assert sf.get_target_hash("//test:a") == "hash123"

    def test_update_target_hash(self):
        """Target hash can be updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash_v1")
            assert sf.get_target_hash("//test:a") == "hash_v1"

            sf.set_target_hash("//test:a", "hash_v2")
            assert sf.get_target_hash("//test:a") == "hash_v2"

    def test_target_hash_survives_roundtrip(self):
        """Target hash persists through save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf1 = StatusFile(path)
            sf1.set_test_state("//test:a", "stable")
            sf1.set_target_hash("//test:a", "hash123")
            sf1.save()

            sf2 = StatusFile(path)
            assert sf2.get_target_hash("//test:a") == "hash123"

    def test_set_test_state_preserves_target_hash(self):
        """set_test_state preserves existing target_hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.set_target_hash("//test:a", "hash123")

            # Transition state (preserve history)
            sf.set_test_state("//test:a", "stable")
            assert sf.get_target_hash("//test:a") == "hash123"

    def test_set_test_state_with_clear_history_preserves_hash(self):
        """set_test_state with clear_history=True still preserves target_hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash123")
            sf.record_run("//test:a", passed=True, commit="abc")

            sf.set_test_state("//test:a", "burning_in", clear_history=True)
            assert sf.get_target_hash("//test:a") == "hash123"
            assert sf.get_test_history("//test:a") == []

    def test_backward_compat_old_format_no_target_hash(self):
        """Old status files without target_hash load without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            data = {
                "tests": {
                    "//test:a": {
                        "state": "stable",
                        "history": [
                            {"passed": True, "commit": "abc"}
                        ],
                        "last_updated": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
            path.write_text(json.dumps(data))

            sf = StatusFile(path)
            assert sf.get_test_state("//test:a") == "stable"
            assert sf.get_target_hash("//test:a") is None
            assert len(sf.get_test_history("//test:a")) == 1


class TestStatusFileInvalidateEvidence:
    """Tests for invalidate_evidence method."""

    def test_invalidate_evidence_clears_history(self):
        """invalidate_evidence clears all history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.record_run("//test:a", True, commit="abc")
            sf.record_run("//test:a", True, commit="def")
            assert len(sf.get_test_history("//test:a")) == 2

            sf.invalidate_evidence("//test:a")
            assert len(sf.get_test_history("//test:a")) == 0

    def test_invalidate_evidence_transitions_to_burning_in(self):
        """invalidate_evidence transitions state to burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.invalidate_evidence("//test:a")
            assert sf.get_test_state("//test:a") == "burning_in"

    def test_invalidate_evidence_updates_last_updated(self):
        """invalidate_evidence updates last_updated timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            entry_before = sf.get_test_entry("//test:a")
            assert entry_before is not None
            ts_before = entry_before["last_updated"]

            # Small delay not needed -- timestamp is ISO format so even
            # same-second calls are fine for a non-equality check
            sf.invalidate_evidence("//test:a")
            entry_after = sf.get_test_entry("//test:a")
            assert entry_after is not None
            assert "last_updated" in entry_after

    def test_invalidate_evidence_preserves_target_hash(self):
        """invalidate_evidence preserves the target_hash field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_target_hash("//test:a", "hash123")
            sf.record_run("//test:a", True, commit="abc")

            sf.invalidate_evidence("//test:a")
            assert sf.get_target_hash("//test:a") == "hash123"
            assert sf.get_test_state("//test:a") == "burning_in"
            assert len(sf.get_test_history("//test:a")) == 0

    def test_invalidate_evidence_nonexistent_test_noop(self):
        """invalidate_evidence for nonexistent test is a no-op."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            # Should not raise
            sf.invalidate_evidence("//test:nonexistent")
            assert sf.get_test_state("//test:nonexistent") is None

    def test_invalidate_evidence_from_flaky(self):
        """invalidate_evidence transitions flaky -> burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "flaky")
            sf.record_run("//test:a", True, commit="abc")
            sf.record_run("//test:a", False, commit="def")

            sf.invalidate_evidence("//test:a")
            assert sf.get_test_state("//test:a") == "burning_in"
            assert len(sf.get_test_history("//test:a")) == 0

    def test_invalidate_evidence_from_burning_in(self):
        """invalidate_evidence on burning_in test clears history, stays burning_in."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="abc")

            sf.invalidate_evidence("//test:a")
            assert sf.get_test_state("//test:a") == "burning_in"
            assert len(sf.get_test_history("//test:a")) == 0


class TestStatusFileSameHashHistory:
    """Tests for get_same_hash_history method."""

    def test_same_hash_filters_matching(self):
        """get_same_hash_history returns only entries with matching hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="c1", target_hash="hash_v1")
            sf.record_run("//test:a", False, commit="c2", target_hash="hash_v2")
            sf.record_run("//test:a", True, commit="c3", target_hash="hash_v1")

            v1_history = sf.get_same_hash_history("//test:a", "hash_v1")
            assert len(v1_history) == 2
            # newest-first order preserved
            assert v1_history[0]["commit"] == "c3"
            assert v1_history[1]["commit"] == "c1"

    def test_same_hash_excludes_no_hash_entries(self):
        """Entries without target_hash are excluded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="c1")  # no hash
            sf.record_run("//test:a", True, commit="c2", target_hash="hash_v1")
            sf.record_run("//test:a", True, commit="c3")  # no hash

            v1_history = sf.get_same_hash_history("//test:a", "hash_v1")
            assert len(v1_history) == 1
            assert v1_history[0]["commit"] == "c2"

    def test_same_hash_no_matches(self):
        """No matching hash entries returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="c1", target_hash="hash_v1")

            assert sf.get_same_hash_history("//test:a", "hash_v2") == []

    def test_same_hash_nonexistent_test(self):
        """Nonexistent test returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            assert sf.get_same_hash_history("//test:nonexistent", "hash") == []

    def test_same_hash_empty_history(self):
        """Test with no history returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            assert sf.get_same_hash_history("//test:a", "hash") == []

    def test_same_hash_all_match(self):
        """All entries with same hash are returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            for i in range(5):
                sf.record_run(
                    "//test:a", True, commit=f"c{i}", target_hash="same_hash"
                )

            result = sf.get_same_hash_history("//test:a", "same_hash")
            assert len(result) == 5

    def test_same_hash_preserves_order(self):
        """Filtered results preserve newest-first order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="old", target_hash="h1")
            sf.record_run("//test:a", False, commit="mid", target_hash="h2")
            sf.record_run("//test:a", True, commit="new", target_hash="h1")

            result = sf.get_same_hash_history("//test:a", "h1")
            assert result[0]["commit"] == "new"
            assert result[1]["commit"] == "old"

    def test_same_hash_survives_roundtrip(self):
        """Hash-tagged history entries survive save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf1 = StatusFile(path)
            sf1.set_test_state("//test:a", "burning_in")
            sf1.record_run("//test:a", True, commit="c1", target_hash="hash_v1")
            sf1.record_run("//test:a", False, commit="c2", target_hash="hash_v2")
            sf1.save()

            sf2 = StatusFile(path)
            v1 = sf2.get_same_hash_history("//test:a", "hash_v1")
            assert len(v1) == 1
            assert v1[0]["commit"] == "c1"
            assert v1[0]["target_hash"] == "hash_v1"

    def test_same_hash_with_runs_and_passes(self):
        """Runs and passes can be derived from same-hash filtered history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            # 3 runs with hash_v1: 2 pass, 1 fail
            sf.record_run("//test:a", True, commit="c1", target_hash="hash_v1")
            sf.record_run("//test:a", False, commit="c2", target_hash="hash_v1")
            sf.record_run("//test:a", True, commit="c3", target_hash="hash_v1")
            # 2 runs with hash_v2: all pass
            sf.record_run("//test:a", True, commit="c4", target_hash="hash_v2")
            sf.record_run("//test:a", True, commit="c5", target_hash="hash_v2")

            v1 = sf.get_same_hash_history("//test:a", "hash_v1")
            runs, passes = runs_and_passes_from_history(v1)
            assert runs == 3
            assert passes == 2

            v2 = sf.get_same_hash_history("//test:a", "hash_v2")
            runs, passes = runs_and_passes_from_history(v2)
            assert runs == 2
            assert passes == 2


class TestStatusFileRecordRunWithHash:
    """Tests for record_run with target_hash parameter."""

    def test_record_run_with_target_hash(self):
        """record_run stores target_hash in history entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="abc", target_hash="hash123")

            history = sf.get_test_history("//test:a")
            assert len(history) == 1
            assert history[0]["passed"] is True
            assert history[0]["commit"] == "abc"
            assert history[0]["target_hash"] == "hash123"

    def test_record_run_without_target_hash(self):
        """record_run without target_hash does not add hash to entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="abc")

            history = sf.get_test_history("//test:a")
            assert len(history) == 1
            assert "target_hash" not in history[0]

    def test_record_run_mixed_hash_no_hash(self):
        """History can have mix of entries with and without target_hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.record_run("//test:a", True, commit="c1")
            sf.record_run("//test:a", True, commit="c2", target_hash="hash_v1")
            sf.record_run("//test:a", False, commit="c3")

            history = sf.get_test_history("//test:a")
            assert len(history) == 3
            # newest-first
            assert "target_hash" not in history[0]  # c3
            assert history[1]["target_hash"] == "hash_v1"  # c2
            assert "target_hash" not in history[2]  # c1

    def test_record_run_hash_survives_roundtrip(self):
        """Target hash in history entries persists through save/load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "status.json"
            sf1 = StatusFile(path)
            sf1.set_test_state("//test:a", "burning_in")
            sf1.record_run("//test:a", True, commit="abc", target_hash="hash123")
            sf1.save()

            sf2 = StatusFile(path)
            history = sf2.get_test_history("//test:a")
            assert history[0]["target_hash"] == "hash123"

    def test_record_run_hash_new_test(self):
        """record_run with hash for new test creates entry with hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.record_run("//test:a", True, commit="abc", target_hash="hash123")

            assert sf.get_test_state("//test:a") == "new"
            history = sf.get_test_history("//test:a")
            assert history[0]["target_hash"] == "hash123"

    def test_record_run_hash_capped(self):
        """History cap applies to entries with target_hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            for i in range(HISTORY_CAP + 10):
                sf.record_run(
                    "//test:a", True, commit=f"c{i}", target_hash=f"h{i}"
                )

            history = sf.get_test_history("//test:a")
            assert len(history) == HISTORY_CAP
            # Newest entry is the last one recorded
            assert history[0]["target_hash"] == f"h{HISTORY_CAP + 9}"


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
