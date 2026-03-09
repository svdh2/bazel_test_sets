"""SQLite storage backend for status data.

Implements ``StorageBackend`` using an in-memory SQLite database.
Data is loaded from and persisted to CSV files (``tests.csv`` and
``history.csv``) so that only git-friendly text is stored on disk.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any

from orchestrator.lifecycle.backend.base import StorageBackend

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tests (
    test_name    TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    target_hash  TEXT,
    last_updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    test_name   TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    commit_sha  TEXT,
    target_hash TEXT,
    FOREIGN KEY (test_name) REFERENCES tests(test_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_test
    ON history(test_name);

CREATE INDEX IF NOT EXISTS idx_history_test_hash
    ON history(test_name, target_hash);
"""

_TESTS_CSV = "tests.csv"
_HISTORY_CSV = "history.csv"
_TESTS_COLUMNS = ["test_name", "state", "target_hash", "last_updated"]
_HISTORY_COLUMNS = ["id", "test_name", "passed", "commit_sha", "target_hash"]


class SqliteBackend(StorageBackend):
    """In-memory SQLite backend with CSV file persistence.

    Data lives in an in-memory SQLite database during operation.
    :meth:`load` populates the database from CSV files and :meth:`persist`
    dumps the database back to CSV so that only text files are stored in git.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.initialize()

    # -- lifecycle -----------------------------------------------------------

    def initialize(self) -> None:
        self._conn.executescript(_SCHEMA)

    def load(self, directory: Path) -> None:
        self._load_tests_csv(directory / _TESTS_CSV)
        self._load_history_csv(directory / _HISTORY_CSV)

    def persist(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._dump_tests_csv(directory / _TESTS_CSV)
        self._dump_history_csv(directory / _HISTORY_CSV)

    # -- CSV load ------------------------------------------------------------

    def _load_tests_csv(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows: list[tuple[Any, ...]] = []
                for row in reader:
                    rows.append((
                        row["test_name"],
                        row["state"],
                        row["target_hash"] or None,
                        row["last_updated"],
                    ))
            self._conn.executemany(
                "INSERT OR REPLACE INTO tests"
                " (test_name, state, target_hash, last_updated)"
                " VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        except (csv.Error, KeyError, OSError):
            self._conn.execute("DELETE FROM tests")
            self._conn.commit()

    def _load_history_csv(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows: list[tuple[Any, ...]] = []
                for row in reader:
                    rows.append((
                        int(row["id"]),
                        row["test_name"],
                        int(row["passed"]),
                        row["commit_sha"] or None,
                        row["target_hash"] or None,
                    ))
            self._conn.executemany(
                "INSERT INTO history"
                " (id, test_name, passed, commit_sha, target_hash)"
                " VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        except (csv.Error, KeyError, ValueError, OSError):
            self._conn.execute("DELETE FROM history")
            self._conn.commit()

    # -- CSV dump ------------------------------------------------------------

    def _dump_tests_csv(self, path: Path) -> None:
        rows = self._conn.execute(
            "SELECT test_name, state, target_hash, last_updated"
            " FROM tests ORDER BY test_name"
        ).fetchall()
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(_TESTS_COLUMNS)
            for row in rows:
                writer.writerow([
                    row[0],
                    row[1],
                    row[2] or "",
                    row[3],
                ])

    def _dump_history_csv(self, path: Path) -> None:
        rows = self._conn.execute(
            "SELECT id, test_name, passed, commit_sha, target_hash"
            " FROM history ORDER BY id"
        ).fetchall()
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(_HISTORY_COLUMNS)
            for row in rows:
                writer.writerow([
                    row[0],
                    row[1],
                    row[2],
                    row[3] or "",
                    row[4] or "",
                ])

    # -- test CRUD -----------------------------------------------------------

    def get_test(self, test_name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT state, target_hash, last_updated"
            " FROM tests WHERE test_name = ?",
            (test_name,),
        ).fetchone()
        if row is None:
            return None
        return {
            "state": row[0],
            "target_hash": row[1],
            "last_updated": row[2],
        }

    def upsert_test(
        self,
        test_name: str,
        state: str,
        target_hash: str | None,
        last_updated: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO tests (test_name, state, target_hash, last_updated)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(test_name) DO UPDATE SET"
            "   state = excluded.state,"
            "   target_hash = excluded.target_hash,"
            "   last_updated = excluded.last_updated",
            (test_name, state, target_hash, last_updated),
        )
        self._conn.commit()

    def remove_test(self, test_name: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM tests WHERE test_name = ?",
            (test_name,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_all_tests(self) -> dict[str, dict[str, Any]]:
        test_rows = self._conn.execute(
            "SELECT test_name, state, target_hash, last_updated FROM tests"
        ).fetchall()

        # Batch-load all history in one query, grouped by test_name.
        history_rows = self._conn.execute(
            "SELECT test_name, passed, commit_sha, target_hash"
            " FROM history ORDER BY id DESC"
        ).fetchall()
        history_map: dict[str, list[dict[str, Any]]] = {}
        for row in history_rows:
            entry: dict[str, Any] = {
                "passed": bool(row[1]),
                "commit": row[2],
            }
            if row[3] is not None:
                entry["target_hash"] = row[3]
            history_map.setdefault(row[0], []).append(entry)

        result: dict[str, dict[str, Any]] = {}
        for row in test_rows:
            test_name = row[0]
            test_entry: dict[str, Any] = {
                "state": row[1],
                "history": history_map.get(test_name, []),
                "last_updated": row[3],
            }
            if row[2] is not None:
                test_entry["target_hash"] = row[2]
            result[test_name] = test_entry
        return result

    def get_tests_by_state(self, state: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT test_name FROM tests WHERE state = ?",
            (state,),
        ).fetchall()
        return [row[0] for row in rows]

    def test_exists(self, test_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM tests WHERE test_name = ?",
            (test_name,),
        ).fetchone()
        return row is not None

    # -- history CRUD --------------------------------------------------------

    def insert_history(
        self,
        test_name: str,
        passed: bool,
        commit_sha: str | None,
        target_hash: str | None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO history (test_name, passed, commit_sha, target_hash)"
            " VALUES (?, ?, ?, ?)",
            (test_name, int(passed), commit_sha, target_hash),
        )
        self._conn.commit()

    def get_history(self, test_name: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT passed, commit_sha, target_hash"
            " FROM history WHERE test_name = ? ORDER BY id DESC",
            (test_name,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            entry: dict[str, Any] = {
                "passed": bool(row[0]),
                "commit": row[1],
            }
            if row[2] is not None:
                entry["target_hash"] = row[2]
            result.append(entry)
        return result

    def get_same_hash_history(
        self,
        test_name: str,
        target_hash: str,
    ) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT passed, commit_sha, target_hash"
            " FROM history"
            " WHERE test_name = ? AND target_hash = ?"
            " ORDER BY id DESC",
            (test_name, target_hash),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            entry: dict[str, Any] = {
                "passed": bool(row[0]),
                "commit": row[1],
                "target_hash": row[2],
            }
            result.append(entry)
        return result

    def clear_history(self, test_name: str) -> None:
        self._conn.execute(
            "DELETE FROM history WHERE test_name = ?",
            (test_name,),
        )
        self._conn.commit()

    def enforce_history_cap(self, test_name: str, cap: int) -> None:
        self._conn.execute(
            "DELETE FROM history"
            " WHERE test_name = ? AND id NOT IN ("
            "   SELECT id FROM history"
            "   WHERE test_name = ?"
            "   ORDER BY id DESC LIMIT ?"
            " )",
            (test_name, test_name, cap),
        )
        self._conn.commit()

    # -- JSON legacy loading -------------------------------------------------

    def load_from_json_data(self, data: dict[str, Any]) -> None:
        """Populate the backend from a legacy JSON status data dict.

        Args:
            data: Parsed JSON with ``{"tests": {name: {state, history, ...}}}``.
        """
        tests = data.get("tests", {})
        for test_name, entry in tests.items():
            state = entry.get("state", "new")
            target_hash = entry.get("target_hash")
            last_updated = entry.get("last_updated", "")
            self.upsert_test(test_name, state, target_hash, last_updated)

            # Insert history in reverse order (oldest first) so that
            # AUTOINCREMENT ids produce the correct newest-first ordering.
            history = entry.get("history", [])
            for hist_entry in reversed(history):
                self.insert_history(
                    test_name,
                    hist_entry.get("passed", False),
                    hist_entry.get("commit"),
                    hist_entry.get("target_hash"),
                )
