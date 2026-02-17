"""State file management for the burn-in lifecycle.

Reads and writes the .tests/status JSON file that tracks test maturity
states. Configuration is managed separately by TestSetConfig.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from orchestrator.lifecycle.config import DEFAULT_CONFIG, TestSetConfig


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
    """Manages the .tests/status JSON state file.

    The state file tracks per-test state (state, history, last_updated).
    Configuration is delegated to a TestSetConfig instance.
    """

    def __init__(
        self, path: str | Path, config_path: Path | None = None
    ) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"tests": {}}
        self._config = TestSetConfig(config_path)
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        """Load state from the file."""
        try:
            text = self.path.read_text()
            self._data = json.loads(text)
        except (json.JSONDecodeError, OSError):
            # If file is corrupted, start fresh
            self._data = {"tests": {}}

        # Ensure required sections exist
        if "tests" not in self._data:
            self._data["tests"] = {}

    def save(self) -> None:
        """Write state to the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        save_data = {"tests": self._data.get("tests", {})}
        with open(self.path, "w") as f:
            json.dump(save_data, f, indent=2)
            f.write("\n")

    @property
    def config(self) -> dict[str, Any]:
        """Get the configuration section."""
        return self._config.config

    @property
    def min_reliability(self) -> float:
        """Get the minimum reliability threshold."""
        return self._config.min_reliability

    @property
    def statistical_significance(self) -> float:
        """Get the statistical significance level."""
        return self._config.statistical_significance

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
        self._config.set_config(
            min_reliability=min_reliability,
            statistical_significance=statistical_significance,
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
            Dict with state, history, last_updated, or None.
        """
        return self._data["tests"].get(test_name)

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

        existing = self._data["tests"].get(test_name, {})

        entry: dict[str, Any] = {
            "state": state,
            "history": [] if clear_history else existing.get("history", []),
            "last_updated": now,
        }
        self._data["tests"][test_name] = entry

    def record_run(
        self, test_name: str, passed: bool, commit: str | None = None
    ) -> None:
        """Record a test run result.

        Prepends a history entry with the pass/fail result and commit SHA.
        Updates last_updated. If test doesn't exist in state file, creates
        it with state "new".

        Args:
            test_name: Test identifier.
            passed: Whether the test passed.
            commit: Git commit SHA the run belongs to, or None.
        """
        now = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        if test_name not in self._data["tests"]:
            self._data["tests"][test_name] = {
                "state": "new",
                "history": [],
                "last_updated": now,
            }

        entry = self._data["tests"][test_name]
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
            Dict of {test_name: {state, history, last_updated}}.
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
