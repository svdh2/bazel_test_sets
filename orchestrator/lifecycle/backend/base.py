"""Abstract storage backend for status data.

Defines the ``StorageBackend`` interface that concrete implementations
(e.g. SQLite, PostgreSQL) must satisfy.  The interface covers test CRUD,
history CRUD, durable load/persist, and legacy JSON loading.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StorageBackend(ABC):
    """Abstract base class for status storage backends.

    Implementations manage the database connection and schema, and provide
    CRUD operations for the ``tests`` and ``history`` tables.  Each backend
    also defines how data is loaded from and persisted to durable storage
    (e.g. CSV files for SQLite, native persistence for server databases).
    """

    # -- lifecycle -----------------------------------------------------------

    @abstractmethod
    def initialize(self) -> None:
        """Create the schema (tables/indexes) if not already present."""

    @abstractmethod
    def load(self, directory: Path) -> None:
        """Load data from durable storage into the backend.

        Args:
            directory: Directory containing the data files.
        """

    @abstractmethod
    def persist(self, directory: Path) -> None:
        """Persist backend data to durable storage.

        Args:
            directory: Directory to write data files into.
        """

    # -- test CRUD -----------------------------------------------------------

    @abstractmethod
    def get_test(self, test_name: str) -> dict[str, Any] | None:
        """Get a test entry (state, target_hash, last_updated).

        Returns:
            Dict with ``state``, ``target_hash``, ``last_updated`` keys,
            or None if the test does not exist.
        """

    @abstractmethod
    def upsert_test(
        self,
        test_name: str,
        state: str,
        target_hash: str | None,
        last_updated: str,
    ) -> None:
        """Insert or update a test entry."""

    @abstractmethod
    def remove_test(self, test_name: str) -> bool:
        """Remove a test and its history.

        Returns:
            True if the test existed and was removed.
        """

    @abstractmethod
    def get_all_tests(self) -> dict[str, dict[str, Any]]:
        """Get all test entries with their history arrays.

        Returns:
            ``{test_name: {state, history, last_updated, [target_hash]}}``
        """

    @abstractmethod
    def get_tests_by_state(self, state: str) -> list[str]:
        """Get test names filtered by state."""

    @abstractmethod
    def test_exists(self, test_name: str) -> bool:
        """Check whether a test entry exists."""

    # -- history CRUD --------------------------------------------------------

    @abstractmethod
    def insert_history(
        self,
        test_name: str,
        passed: bool,
        commit_sha: str | None,
        target_hash: str | None,
    ) -> None:
        """Append a history entry (newest = highest id)."""

    @abstractmethod
    def get_history(self, test_name: str) -> list[dict[str, Any]]:
        """Get history for a test, newest-first.

        Each entry: ``{passed: bool, commit: str|None, [target_hash: str]}``.
        """

    @abstractmethod
    def get_same_hash_history(
        self,
        test_name: str,
        target_hash: str,
    ) -> list[dict[str, Any]]:
        """Get history entries with a matching target hash, newest-first."""

    @abstractmethod
    def clear_history(self, test_name: str) -> None:
        """Delete all history entries for a test."""

    @abstractmethod
    def enforce_history_cap(self, test_name: str, cap: int) -> None:
        """Keep only the newest *cap* history entries for a test."""

    # -- legacy loading ------------------------------------------------------

    @abstractmethod
    def load_from_json_data(self, data: dict[str, Any]) -> None:
        """Populate the backend from a legacy JSON status data dict.

        Args:
            data: Parsed JSON with ``{"tests": {name: {state, history, ...}}}``.
        """
