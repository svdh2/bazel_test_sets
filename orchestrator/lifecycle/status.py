"""State file management for the burn-in lifecycle.

Reads and writes the .tests/status JSON file that tracks test maturity
states, run counts, and configuration.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any


# Valid burn-in states
VALID_STATES = frozenset({"new", "burning_in", "stable", "flaky"})

# Default configuration values
DEFAULT_CONFIG = {
    "min_reliability": 0.99,
    "statistical_significance": 0.95,
}

# Maximum per-test history entries (newest-first, oldest dropped when exceeded)
HISTORY_CAP = 200


class StatusFile:
    """Manages the .tests/status JSON state file.

    The state file tracks:
    - Configuration (min_reliability, statistical_significance)
    - Per-test state (state, runs, passes, last_updated)
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"config": dict(DEFAULT_CONFIG), "tests": {}}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        """Load state from the file."""
        try:
            text = self.path.read_text()
            self._data = json.loads(text)
        except (json.JSONDecodeError, OSError):
            # If file is corrupted, start fresh
            self._data = {"config": dict(DEFAULT_CONFIG), "tests": {}}

        # Ensure required sections exist
        if "config" not in self._data:
            self._data["config"] = dict(DEFAULT_CONFIG)
        if "tests" not in self._data:
            self._data["tests"] = {}

    def save(self) -> None:
        """Write state to the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    @property
    def config(self) -> dict[str, Any]:
        """Get the configuration section."""
        return self._data["config"]

    @property
    def min_reliability(self) -> float:
        """Get the minimum reliability threshold."""
        return float(
            self._data["config"].get(
                "min_reliability", DEFAULT_CONFIG["min_reliability"]
            )
        )

    @property
    def statistical_significance(self) -> float:
        """Get the statistical significance level."""
        return float(
            self._data["config"].get(
                "statistical_significance",
                DEFAULT_CONFIG["statistical_significance"],
            )
        )

    def set_config(
        self,
        min_reliability: float | None = None,
        statistical_significance: float | None = None,
    ) -> None:
        """Update configuration values.

        Args:
            min_reliability: New minimum reliability threshold.
            statistical_significance: New significance level.
        """
        if min_reliability is not None:
            self._data["config"]["min_reliability"] = min_reliability
        if statistical_significance is not None:
            self._data["config"]["statistical_significance"] = (
                statistical_significance
            )

    def get_test_state(self, test_name: str) -> str | None:
        """Get the burn-in state of a test.

        Args:
            test_name: Test identifier (e.g., "//test:a").

        Returns:
            State string or None if test not in state file.
        """
        entry = self._data["tests"].get(test_name)
        if entry is None:
            return None
        return entry.get("state")

    def get_test_entry(self, test_name: str) -> dict[str, Any] | None:
        """Get the full state entry for a test.

        Args:
            test_name: Test identifier.

        Returns:
            Dict with state, runs, passes, last_updated, or None.
        """
        return self._data["tests"].get(test_name)

    def set_test_state(
        self,
        test_name: str,
        state: str,
        runs: int | None = None,
        passes: int | None = None,
    ) -> None:
        """Set or update the state of a test.

        Args:
            test_name: Test identifier.
            state: New state (must be in VALID_STATES).
            runs: Total run count (optional, preserved if not specified).
            passes: Total pass count (optional, preserved if not specified).

        Raises:
            ValueError: If state is not a valid burn-in state.
        """
        if state not in VALID_STATES:
            raise ValueError(
                f"Invalid state '{state}'. Must be one of: {sorted(VALID_STATES)}"
            )

        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        existing = self._data["tests"].get(test_name, {})

        # When counters are explicitly reset to zero, clear history too
        # (fresh burn-in cycle after deflake or initial burn-in).
        reset = runs == 0 and passes == 0
        entry: dict[str, Any] = {
            "state": state,
            "runs": runs if runs is not None else existing.get("runs", 0),
            "passes": (
                passes if passes is not None else existing.get("passes", 0)
            ),
            "history": [] if reset else existing.get("history", []),
            "last_updated": now,
        }
        self._data["tests"][test_name] = entry

    def record_run(
        self, test_name: str, passed: bool, commit: str | None = None
    ) -> None:
        """Record a test run result.

        Increments run count and optionally pass count. Prepends a history
        entry with the pass/fail result and commit SHA. Updates last_updated.
        If test doesn't exist in state file, creates it with state "new".

        Args:
            test_name: Test identifier.
            passed: Whether the test passed.
            commit: Git commit SHA the run belongs to, or None.
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        if test_name not in self._data["tests"]:
            self._data["tests"][test_name] = {
                "state": "new",
                "runs": 0,
                "passes": 0,
                "history": [],
                "last_updated": now,
            }

        entry = self._data["tests"][test_name]
        entry["runs"] = entry.get("runs", 0) + 1
        if passed:
            entry["passes"] = entry.get("passes", 0) + 1
        entry["last_updated"] = now

        # Prepend to history (newest-first) and cap
        history = entry.get("history", [])
        history.insert(0, {"passed": passed, "commit": commit})
        entry["history"] = history[:HISTORY_CAP]

    def get_test_history(self, test_name: str) -> list[dict[str, Any]]:
        """Get the run history for a test (newest-first).

        Each entry is {"passed": bool, "commit": str | None}.

        Args:
            test_name: Test identifier.

        Returns:
            List of history entries, or empty list if not found.
        """
        entry = self._data["tests"].get(test_name)
        if entry is None:
            return []
        return list(entry.get("history", []))

    def get_all_tests(self) -> dict[str, dict[str, Any]]:
        """Get all test entries.

        Returns:
            Dict of {test_name: {state, runs, passes, last_updated}}.
        """
        return dict(self._data["tests"])

    def get_tests_by_state(self, state: str) -> list[str]:
        """Get all test names with a given state.

        Args:
            state: State to filter by.

        Returns:
            List of test names.
        """
        return [
            name
            for name, entry in self._data["tests"].items()
            if entry.get("state") == state
        ]

    def remove_test(self, test_name: str) -> bool:
        """Remove a test from the state file.

        Args:
            test_name: Test identifier.

        Returns:
            True if the test was removed, False if not found.
        """
        if test_name in self._data["tests"]:
            del self._data["tests"][test_name]
            return True
        return False
