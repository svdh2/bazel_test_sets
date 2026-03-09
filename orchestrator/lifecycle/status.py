"""State file management for the burn-in lifecycle.

Manages test maturity state via a modular storage backend.  The default
``SqliteBackend`` stores data in an in-memory SQLite database and
persists it to CSV files (``tests.csv`` and ``history.csv``) inside a
directory, keeping only git-friendly text files on disk.

Statistical parameters (min_reliability, statistical_significance) are
passed directly to the StatusFile constructor.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from orchestrator.lifecycle.backend import SqliteBackend, StorageBackend
from orchestrator.lifecycle.config import DEFAULT_CONFIG


# Valid burn-in states
VALID_STATES = frozenset({"new", "burning_in", "stable", "flaky", "disabled"})

# Maximum per-test history entries (newest-first, oldest dropped when exceeded)
HISTORY_CAP = 200


def runs_and_passes_from_history(
    history: list[dict[str, Any]],
) -> tuple[int, int]:
    """Derive runs and passes counts from the history array.

    Args:
        history: List of {"passed": bool, "commit": str | None} entries.

    Returns:
        Tuple of (runs, passes).
    """
    runs = len(history)
    passes = sum(1 for entry in history if entry.get("passed", False))
    return runs, passes


class StatusFile:
    """Manages test maturity state via a storage backend.

    The state directory (``path``) holds CSV files consumed by the backend.
    Statistical parameters are stored directly as instance attributes.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        min_reliability: float | None = None,
        statistical_significance: float | None = None,
        engine: StorageBackend | None = None,
    ) -> None:
        self.path = Path(path)
        self._min_reliability = (
            min_reliability
            if min_reliability is not None
            else DEFAULT_CONFIG["min_reliability"]
        )
        self._statistical_significance = (
            statistical_significance
            if statistical_significance is not None
            else DEFAULT_CONFIG["statistical_significance"]
        )
        self._engine = engine or SqliteBackend()
        self._load()

    def _load(self) -> None:
        """Load state from CSV directory or legacy JSON file."""
        if self.path.is_dir():
            self._engine.load(self.path)
        elif self.path.is_file():
            self._load_json_legacy()

    def _load_json_legacy(self) -> None:
        """Load from a legacy JSON status file."""
        try:
            text = self.path.read_text()
            data = json.loads(text)
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        self._engine.load_from_json_data(data)

    def save(self) -> None:
        """Persist state to CSV files in the directory.

        If ``path`` was previously a legacy JSON file it is removed first
        so the directory can be created in its place.
        """
        if self.path.is_file():
            self.path.unlink()
        self.path.mkdir(parents=True, exist_ok=True)
        self._engine.persist(self.path)

    @property
    def min_reliability(self) -> float:
        """Get the minimum reliability threshold."""
        return self._min_reliability

    @property
    def statistical_significance(self) -> float:
        """Get the statistical significance level."""
        return self._statistical_significance

    def set_config(
        self,
        min_reliability: float | None = None,
        statistical_significance: float | None = None,
    ) -> None:
        """Update statistical parameters in memory.

        Args:
            min_reliability: New minimum reliability threshold.
            statistical_significance: New significance level.
        """
        if min_reliability is not None:
            self._min_reliability = min_reliability
        if statistical_significance is not None:
            self._statistical_significance = statistical_significance

    def get_test_state(self, test_name: str) -> str | None:
        """Get the burn-in state of a test.

        Args:
            test_name: Test identifier (e.g., "//test:a").

        Returns:
            State string or None if test not in state file.
        """
        entry = self._engine.get_test(test_name)
        if entry is None:
            return None
        return entry.get("state")

    def get_test_entry(self, test_name: str) -> dict[str, Any] | None:
        """Get the full state entry for a test.

        Args:
            test_name: Test identifier.

        Returns:
            Dict with state, history, last_updated, or None.
        """
        entry = self._engine.get_test(test_name)
        if entry is None:
            return None
        result: dict[str, Any] = {
            "state": entry["state"],
            "history": self._engine.get_history(test_name),
            "last_updated": entry["last_updated"],
        }
        if entry.get("target_hash") is not None:
            result["target_hash"] = entry["target_hash"]
        return result

    def set_test_state(
        self,
        test_name: str,
        state: str,
        *,
        clear_history: bool = False,
    ) -> None:
        """Set or update the state of a test.

        Args:
            test_name: Test identifier.
            state: New state (must be in VALID_STATES).
            clear_history: If True, clear run history (fresh burn-in cycle).

        Raises:
            ValueError: If state is not a valid burn-in state.
        """
        if state not in VALID_STATES:
            raise ValueError(
                f"Invalid state '{state}'. Must be one of: {sorted(VALID_STATES)}"
            )

        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        existing = self._engine.get_test(test_name)
        target_hash = existing.get("target_hash") if existing else None

        self._engine.upsert_test(test_name, state, target_hash, now)
        if clear_history:
            self._engine.clear_history(test_name)

    def get_target_hash(self, test_name: str) -> str | None:
        """Get the stored target hash for a test.

        Args:
            test_name: Test identifier (e.g., "//test:a").

        Returns:
            Hash string, or None if test not found or no hash stored.
        """
        entry = self._engine.get_test(test_name)
        if entry is None:
            return None
        return entry.get("target_hash")

    def set_target_hash(self, test_name: str, hash_value: str) -> None:
        """Set the target hash for a test.

        If the test does not exist in the status file, creates it with
        state "new".

        Args:
            test_name: Test identifier.
            hash_value: Hash string to store.
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        existing = self._engine.get_test(test_name)
        if existing is None:
            self._engine.upsert_test(test_name, "new", hash_value, now)
        else:
            self._engine.upsert_test(
                test_name, existing["state"], hash_value, existing["last_updated"]
            )

    def clear_target_hash(self, test_name: str) -> None:
        """Clear the target hash for a test.

        If the test does not exist this is a no-op.

        Args:
            test_name: Test identifier.
        """
        existing = self._engine.get_test(test_name)
        if existing is None:
            return
        self._engine.upsert_test(
            test_name, existing["state"], None, existing["last_updated"]
        )

    def invalidate_evidence(self, test_name: str) -> None:
        """Invalidate SPRT evidence for a test due to hash change.

        Clears history, transitions state to ``burning_in``, and updates
        ``last_updated``.  The test entry is preserved (including its new
        target_hash if one was set separately).

        If the test does not exist in the status file this is a no-op.

        Args:
            test_name: Test identifier.
        """
        existing = self._engine.get_test(test_name)
        if existing is None:
            return

        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        self._engine.upsert_test(
            test_name, "burning_in", existing.get("target_hash"), now
        )
        self._engine.clear_history(test_name)

    def get_same_hash_history(
        self, test_name: str, target_hash: str
    ) -> list[dict[str, Any]]:
        """Get history entries recorded with a matching target hash.

        Filters the test's history to only entries whose ``target_hash``
        field equals *target_hash*.  Entries without a ``target_hash``
        field are excluded.

        Args:
            test_name: Test identifier.
            target_hash: The hash to match against.

        Returns:
            Filtered list of history entries (newest-first order preserved).
        """
        return self._engine.get_same_hash_history(test_name, target_hash)

    def record_run(
        self,
        test_name: str,
        passed: bool,
        commit: str | None = None,
        *,
        target_hash: str | None = None,
    ) -> None:
        """Record a test run result.

        Appends a history entry with the pass/fail result, commit SHA,
        and optional target hash.  Updates last_updated. If test doesn't
        exist in state file, creates it with state "new".

        Args:
            test_name: Test identifier.
            passed: Whether the test passed.
            commit: Git commit SHA the run belongs to, or None.
            target_hash: Target content hash for this run, or None.
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        if not self._engine.test_exists(test_name):
            self._engine.upsert_test(test_name, "new", None, now)
        else:
            existing = self._engine.get_test(test_name)
            assert existing is not None
            self._engine.upsert_test(
                test_name, existing["state"], existing.get("target_hash"), now
            )

        self._engine.insert_history(test_name, passed, commit, target_hash)
        self._engine.enforce_history_cap(test_name, HISTORY_CAP)

    def get_test_history(self, test_name: str) -> list[dict[str, Any]]:
        """Get the run history for a test (newest-first).

        Each entry is {"passed": bool, "commit": str | None}.

        Args:
            test_name: Test identifier.

        Returns:
            List of history entries, or empty list if not found.
        """
        return self._engine.get_history(test_name)

    def get_all_tests(self) -> dict[str, dict[str, Any]]:
        """Get all test entries.

        Returns:
            Dict of {test_name: {state, history, last_updated}}.
        """
        return self._engine.get_all_tests()

    def get_tests_by_state(self, state: str) -> list[str]:
        """Get all test names with a given state.

        Args:
            state: State to filter by.

        Returns:
            List of test names.
        """
        return self._engine.get_tests_by_state(state)

    def remove_test(self, test_name: str) -> bool:
        """Remove a test from the state file.

        Args:
            test_name: Test identifier.

        Returns:
            True if the test was removed, False if not found.
        """
        return self._engine.remove_test(test_name)
