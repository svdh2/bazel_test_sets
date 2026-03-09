"""Unit tests for the SQLite storage backend."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orchestrator.lifecycle.backend import SqliteBackend


class TestSqliteBackendSchema:
    """Tests for schema initialization."""

    def test_initialize_creates_tables(self):
        """Backend creates tests and history tables."""
        backend = SqliteBackend()
        # Should be able to query both tables without error
        assert backend.get_all_tests() == {}
        assert backend.get_history("//test:a") == []

    def test_double_initialize_is_safe(self):
        """Calling initialize twice does not raise."""
        backend = SqliteBackend()
        backend.initialize()
        assert backend.get_all_tests() == {}


class TestSqliteBackendTestCRUD:
    """Tests for test table operations."""

    def test_upsert_and_get(self):
        """Insert a test and retrieve it."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "stable", "hash1", "2026-01-01T00:00:00+00:00")
        entry = backend.get_test("//test:a")
        assert entry is not None
        assert entry["state"] == "stable"
        assert entry["target_hash"] == "hash1"
        assert entry["last_updated"] == "2026-01-01T00:00:00+00:00"

    def test_get_nonexistent(self):
        """Getting a nonexistent test returns None."""
        backend = SqliteBackend()
        assert backend.get_test("//test:missing") is None

    def test_upsert_updates_existing(self):
        """Upserting an existing test updates all fields."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "new", None, "2026-01-01T00:00:00+00:00")
        backend.upsert_test("//test:a", "stable", "hash2", "2026-01-02T00:00:00+00:00")
        entry = backend.get_test("//test:a")
        assert entry is not None
        assert entry["state"] == "stable"
        assert entry["target_hash"] == "hash2"

    def test_upsert_can_set_hash_to_none(self):
        """Upserting with target_hash=None clears the hash."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "stable", "hash1", "2026-01-01T00:00:00+00:00")
        backend.upsert_test("//test:a", "stable", None, "2026-01-01T00:00:00+00:00")
        entry = backend.get_test("//test:a")
        assert entry is not None
        assert entry["target_hash"] is None

    def test_remove_existing(self):
        """Removing an existing test returns True."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "stable", None, "2026-01-01T00:00:00+00:00")
        assert backend.remove_test("//test:a") is True
        assert backend.get_test("//test:a") is None

    def test_remove_nonexistent(self):
        """Removing a nonexistent test returns False."""
        backend = SqliteBackend()
        assert backend.remove_test("//test:missing") is False

    def test_remove_cascades_history(self):
        """Removing a test deletes its history."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "stable", None, "2026-01-01T00:00:00+00:00")
        backend.insert_history("//test:a", True, "abc", None)
        backend.insert_history("//test:a", False, "def", None)
        assert len(backend.get_history("//test:a")) == 2

        backend.remove_test("//test:a")
        assert backend.get_history("//test:a") == []

    def test_test_exists(self):
        """test_exists returns correct boolean."""
        backend = SqliteBackend()
        assert backend.test_exists("//test:a") is False
        backend.upsert_test("//test:a", "new", None, "2026-01-01T00:00:00+00:00")
        assert backend.test_exists("//test:a") is True

    def test_get_tests_by_state(self):
        """Filter tests by state."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "stable", None, "t1")
        backend.upsert_test("//test:b", "burning_in", None, "t2")
        backend.upsert_test("//test:c", "stable", None, "t3")

        stable = backend.get_tests_by_state("stable")
        assert sorted(stable) == ["//test:a", "//test:c"]
        assert backend.get_tests_by_state("burning_in") == ["//test:b"]
        assert backend.get_tests_by_state("flaky") == []

    def test_get_all_tests(self):
        """Get all tests with history."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "stable", "h1", "t1")
        backend.insert_history("//test:a", True, "abc", "h1")
        backend.upsert_test("//test:b", "new", None, "t2")

        all_tests = backend.get_all_tests()
        assert len(all_tests) == 2

        a_entry = all_tests["//test:a"]
        assert a_entry["state"] == "stable"
        assert a_entry["target_hash"] == "h1"
        assert len(a_entry["history"]) == 1
        assert a_entry["history"][0]["passed"] is True

        b_entry = all_tests["//test:b"]
        assert b_entry["state"] == "new"
        assert "target_hash" not in b_entry
        assert b_entry["history"] == []

    def test_get_all_tests_empty(self):
        """Empty backend returns empty dict."""
        backend = SqliteBackend()
        assert backend.get_all_tests() == {}


class TestSqliteBackendHistory:
    """Tests for history table operations."""

    def test_insert_and_get_history(self):
        """Insert history entries and retrieve newest-first."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "aaa", None)
        backend.insert_history("//test:a", False, "bbb", None)
        backend.insert_history("//test:a", True, "ccc", None)

        history = backend.get_history("//test:a")
        assert len(history) == 3
        assert history[0] == {"passed": True, "commit": "ccc"}
        assert history[1] == {"passed": False, "commit": "bbb"}
        assert history[2] == {"passed": True, "commit": "aaa"}

    def test_history_nonexistent_test(self):
        """History for nonexistent test returns empty list."""
        backend = SqliteBackend()
        assert backend.get_history("//test:missing") == []

    def test_history_target_hash_presence(self):
        """target_hash key is only present when not NULL."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "c1", None)
        backend.insert_history("//test:a", True, "c2", "hash_v1")

        history = backend.get_history("//test:a")
        assert "target_hash" not in history[1]  # c1 (oldest, no hash)
        assert history[0]["target_hash"] == "hash_v1"  # c2 (newest)

    def test_clear_history(self):
        """clear_history removes all entries for a test."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "abc", None)
        backend.insert_history("//test:a", False, "def", None)
        assert len(backend.get_history("//test:a")) == 2

        backend.clear_history("//test:a")
        assert backend.get_history("//test:a") == []

    def test_clear_history_does_not_affect_other_tests(self):
        """Clearing history for one test does not affect another."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.upsert_test("//test:b", "burning_in", None, "t2")
        backend.insert_history("//test:a", True, "abc", None)
        backend.insert_history("//test:b", True, "def", None)

        backend.clear_history("//test:a")
        assert backend.get_history("//test:a") == []
        assert len(backend.get_history("//test:b")) == 1

    def test_enforce_history_cap(self):
        """enforce_history_cap keeps only the newest entries."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        for i in range(10):
            backend.insert_history("//test:a", True, f"c{i}", None)

        backend.enforce_history_cap("//test:a", 5)
        history = backend.get_history("//test:a")
        assert len(history) == 5
        # Newest entry should be c9
        assert history[0]["commit"] == "c9"
        # Oldest kept should be c5
        assert history[4]["commit"] == "c5"

    def test_enforce_history_cap_noop_when_under(self):
        """enforce_history_cap is a no-op when count <= cap."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "c1", None)
        backend.enforce_history_cap("//test:a", 200)
        assert len(backend.get_history("//test:a")) == 1


class TestSqliteBackendSameHashHistory:
    """Tests for hash-filtered history."""

    def test_same_hash_filters(self):
        """get_same_hash_history returns only matching entries."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "c1", "hash_v1")
        backend.insert_history("//test:a", False, "c2", "hash_v2")
        backend.insert_history("//test:a", True, "c3", "hash_v1")

        v1 = backend.get_same_hash_history("//test:a", "hash_v1")
        assert len(v1) == 2
        assert v1[0]["commit"] == "c3"
        assert v1[1]["commit"] == "c1"

    def test_same_hash_excludes_null_hash(self):
        """Entries without target_hash are excluded."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "c1", None)
        backend.insert_history("//test:a", True, "c2", "hash_v1")

        assert len(backend.get_same_hash_history("//test:a", "hash_v1")) == 1

    def test_same_hash_no_matches(self):
        """No matching hash returns empty list."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "c1", "hash_v1")
        assert backend.get_same_hash_history("//test:a", "hash_v2") == []

    def test_same_hash_nonexistent_test(self):
        """Nonexistent test returns empty list."""
        backend = SqliteBackend()
        assert backend.get_same_hash_history("//test:missing", "hash") == []

    def test_same_hash_includes_hash_in_result(self):
        """Result entries include the target_hash key."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "burning_in", None, "t1")
        backend.insert_history("//test:a", True, "c1", "hash_v1")

        result = backend.get_same_hash_history("//test:a", "hash_v1")
        assert result[0]["target_hash"] == "hash_v1"


class TestSqliteBackendCSVRoundtrip:
    """Tests for CSV load/persist."""

    def test_roundtrip_tests(self):
        """Tests table survives CSV round-trip."""
        backend1 = SqliteBackend()
        backend1.upsert_test("//test:a", "stable", "h1", "t1")
        backend1.upsert_test("//test:b", "flaky", None, "t2")

        with tempfile.TemporaryDirectory() as tmpdir:
            backend1.persist(Path(tmpdir))

            backend2 = SqliteBackend()
            backend2.load(Path(tmpdir))

            a = backend2.get_test("//test:a")
            assert a is not None
            assert a["state"] == "stable"
            assert a["target_hash"] == "h1"

            b = backend2.get_test("//test:b")
            assert b is not None
            assert b["state"] == "flaky"
            assert b["target_hash"] is None

    def test_roundtrip_history(self):
        """History table survives CSV round-trip with order preserved."""
        backend1 = SqliteBackend()
        backend1.upsert_test("//test:a", "burning_in", None, "t1")
        backend1.insert_history("//test:a", True, "c1", "hash_v1")
        backend1.insert_history("//test:a", False, "c2", None)
        backend1.insert_history("//test:a", True, "c3", "hash_v1")

        with tempfile.TemporaryDirectory() as tmpdir:
            backend1.persist(Path(tmpdir))

            backend2 = SqliteBackend()
            backend2.load(Path(tmpdir))

            history = backend2.get_history("//test:a")
            assert len(history) == 3
            assert history[0]["commit"] == "c3"
            assert history[0]["target_hash"] == "hash_v1"
            assert history[1]["commit"] == "c2"
            assert "target_hash" not in history[1]
            assert history[2]["commit"] == "c1"

    def test_roundtrip_preserves_same_hash_filtering(self):
        """Hash-filtered history works after CSV round-trip."""
        backend1 = SqliteBackend()
        backend1.upsert_test("//test:a", "burning_in", None, "t1")
        backend1.insert_history("//test:a", True, "c1", "h1")
        backend1.insert_history("//test:a", True, "c2", "h2")

        with tempfile.TemporaryDirectory() as tmpdir:
            backend1.persist(Path(tmpdir))
            backend2 = SqliteBackend()
            backend2.load(Path(tmpdir))

            h1 = backend2.get_same_hash_history("//test:a", "h1")
            assert len(h1) == 1
            assert h1[0]["commit"] == "c1"

    def test_load_empty_directory(self):
        """Loading from empty directory is a no-op."""
        backend = SqliteBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            backend.load(Path(tmpdir))
            assert backend.get_all_tests() == {}

    def test_load_nonexistent_directory(self):
        """Loading from non-existent directory is a no-op."""
        backend = SqliteBackend()
        backend.load(Path("/tmp/nonexistent_engine_test_dir"))
        assert backend.get_all_tests() == {}

    def test_persist_creates_directory(self):
        """persist() creates the directory if needed."""
        backend = SqliteBackend()
        backend.upsert_test("//test:a", "new", None, "t1")
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "sub" / "dir"
            backend.persist(target)
            assert (target / "tests.csv").exists()
            assert (target / "history.csv").exists()

    def test_persist_empty_backend(self):
        """Persisting empty backend creates CSV files with headers only."""
        backend = SqliteBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            backend.persist(Path(tmpdir))
            tests_csv = (Path(tmpdir) / "tests.csv").read_text()
            history_csv = (Path(tmpdir) / "history.csv").read_text()
            assert "test_name" in tests_csv
            assert "id" in history_csv
            # Only header row, no data rows
            assert len(tests_csv.strip().splitlines()) == 1
            assert len(history_csv.strip().splitlines()) == 1


class TestSqliteBackendCorruptedCSV:
    """Tests for handling corrupted CSV files."""

    def test_corrupted_tests_csv(self):
        """Corrupted tests.csv starts fresh."""
        backend = SqliteBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "tests.csv").write_text("garbage\nno,proper,columns")
            backend.load(Path(tmpdir))
            assert backend.get_all_tests() == {}

    def test_corrupted_history_csv(self):
        """Corrupted history.csv starts with empty history."""
        backend = SqliteBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write valid tests.csv but corrupted history.csv
            (Path(tmpdir) / "tests.csv").write_text(
                "test_name,state,target_hash,last_updated\n"
                "//test:a,stable,,2026-01-01T00:00:00+00:00\n"
            )
            (Path(tmpdir) / "history.csv").write_text("not,valid,csv,at,all\nbad")
            backend.load(Path(tmpdir))
            assert backend.get_test("//test:a") is not None
            assert backend.get_history("//test:a") == []

    def test_empty_csv_files(self):
        """Empty CSV files (no header) are handled gracefully."""
        backend = SqliteBackend()
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "tests.csv").write_text("")
            (Path(tmpdir) / "history.csv").write_text("")
            backend.load(Path(tmpdir))
            assert backend.get_all_tests() == {}


class TestSqliteBackendJSONLegacy:
    """Tests for loading from legacy JSON data."""

    def test_load_from_json_data(self):
        """Legacy JSON data is loaded correctly."""
        backend = SqliteBackend()
        data = {
            "tests": {
                "//test:a": {
                    "state": "stable",
                    "history": [
                        {"passed": True, "commit": "ccc"},
                        {"passed": False, "commit": "bbb"},
                        {"passed": True, "commit": "aaa"},
                    ],
                    "last_updated": "2026-01-01T00:00:00+00:00",
                    "target_hash": "hash123",
                }
            }
        }
        backend.load_from_json_data(data)

        entry = backend.get_test("//test:a")
        assert entry is not None
        assert entry["state"] == "stable"
        assert entry["target_hash"] == "hash123"

        history = backend.get_history("//test:a")
        assert len(history) == 3
        # Newest-first order preserved
        assert history[0]["commit"] == "ccc"
        assert history[1]["commit"] == "bbb"
        assert history[2]["commit"] == "aaa"

    def test_load_from_json_data_without_history(self):
        """Legacy JSON without history field loads cleanly."""
        backend = SqliteBackend()
        data = {
            "tests": {
                "//test:a": {
                    "state": "stable",
                    "runs": 50,
                    "passes": 50,
                    "last_updated": "2026-01-01T00:00:00+00:00",
                }
            }
        }
        backend.load_from_json_data(data)
        assert backend.get_test("//test:a") is not None
        assert backend.get_history("//test:a") == []

    def test_load_from_json_data_with_target_hash_in_history(self):
        """History entries with target_hash are preserved."""
        backend = SqliteBackend()
        data = {
            "tests": {
                "//test:a": {
                    "state": "burning_in",
                    "history": [
                        {"passed": True, "commit": "c1", "target_hash": "h1"},
                        {"passed": False, "commit": "c2"},
                    ],
                    "last_updated": "2026-01-01T00:00:00+00:00",
                }
            }
        }
        backend.load_from_json_data(data)

        history = backend.get_history("//test:a")
        assert len(history) == 2
        assert history[0]["target_hash"] == "h1"
        assert "target_hash" not in history[1]

        # Same-hash filtering works
        h1 = backend.get_same_hash_history("//test:a", "h1")
        assert len(h1) == 1

    def test_load_from_empty_json(self):
        """Empty JSON data is handled gracefully."""
        backend = SqliteBackend()
        backend.load_from_json_data({})
        assert backend.get_all_tests() == {}
