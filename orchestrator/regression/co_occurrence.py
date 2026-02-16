"""Co-occurrence graph construction from git history.

Builds a bidirectional index of file-commit relationships by analyzing
git log output. Files are classified as source, test, or ignored based
on configurable extensions and patterns. The graph supports incremental
updates by tracking the last processed commit.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Default file classification settings
DEFAULT_SOURCE_EXTENSIONS = [
    ".py", ".java", ".cc", ".go", ".rs", ".ts", ".js", ".bzl",
]
DEFAULT_TEST_PATTERNS = [
    "*_test.*", "test_*.*", "*_spec.*",
]


def classify_file(
    filepath: str,
    source_extensions: list[str] | None = None,
    test_patterns: list[str] | None = None,
) -> str:
    """Classify a file as source, test, or ignored.

    A file is classified as:
    - "test" if it matches any test pattern
    - "source" if its extension matches any source extension
    - "ignored" otherwise

    Test patterns are checked first, so a file like "auth_test.py" is
    classified as "test" even though ".py" is a source extension.

    Args:
        filepath: Relative file path from repository root.
        source_extensions: List of extensions (e.g. [".py", ".java"]).
        test_patterns: List of glob patterns (e.g. ["*_test.*"]).

    Returns:
        One of "source", "test", or "ignored".
    """
    if source_extensions is None:
        source_extensions = DEFAULT_SOURCE_EXTENSIONS
    if test_patterns is None:
        test_patterns = DEFAULT_TEST_PATTERNS

    basename = os.path.basename(filepath)

    # Check test patterns first (test_main.py is a test, not source)
    for pattern in test_patterns:
        if fnmatch.fnmatch(basename, pattern):
            return "test"

    # Check source extensions
    _, ext = os.path.splitext(filepath)
    if ext in source_extensions:
        return "source"

    return "ignored"


def parse_git_log(
    git_output: str,
) -> list[dict[str, Any]]:
    """Parse git log output into structured commit data.

    Expects git log format:
        git log --format='COMMIT %H %aI' --name-only

    Each commit block starts with 'COMMIT <hash> <iso-timestamp>'
    followed by blank line, then file paths, then another blank line.

    Args:
        git_output: Raw stdout from git log command.

    Returns:
        List of dicts with keys: hash, timestamp, files.
    """
    commits: list[dict[str, Any]] = []
    current_commit: dict[str, Any] | None = None

    for line in git_output.splitlines():
        line = line.strip()

        if line.startswith("COMMIT "):
            # Save previous commit if any
            if current_commit is not None:
                commits.append(current_commit)

            parts = line.split(" ", 2)
            if len(parts) >= 3:
                current_commit = {
                    "hash": parts[1],
                    "timestamp": parts[2],
                    "files": [],
                }
            else:
                current_commit = None
        elif line and current_commit is not None:
            current_commit["files"].append(line)

    # Save last commit
    if current_commit is not None:
        commits.append(current_commit)

    return commits


def build_co_occurrence_graph(
    commits: list[dict[str, Any]],
    source_extensions: list[str] | None = None,
    test_patterns: list[str] | None = None,
    existing_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a co-occurrence graph from parsed commit data.

    Constructs a bidirectional index:
    - file_commits: file_path -> list of {commit, timestamp}
    - commit_files: commit_hash -> {timestamp, source_files, test_files}

    Args:
        commits: List of parsed commit dicts (from parse_git_log).
        source_extensions: Extensions for source file classification.
        test_patterns: Patterns for test file classification.
        existing_graph: Optional existing graph for incremental update.

    Returns:
        Graph dict with metadata, file_commits, and commit_files.
    """
    if source_extensions is None:
        source_extensions = DEFAULT_SOURCE_EXTENSIONS
    if test_patterns is None:
        test_patterns = DEFAULT_TEST_PATTERNS

    # Initialize from existing graph or empty
    if existing_graph is not None:
        file_commits: dict[str, list[dict[str, str]]] = dict(
            existing_graph.get("file_commits", {})
        )
        commit_files: dict[str, dict[str, Any]] = dict(
            existing_graph.get("commit_files", {})
        )
        total_analyzed = existing_graph.get("metadata", {}).get(
            "total_commits_analyzed", 0
        )
    else:
        file_commits = {}
        commit_files = {}
        total_analyzed = 0

    last_commit_hash = None
    new_commits_count = 0

    for commit in commits:
        commit_hash = commit["hash"]
        timestamp = commit["timestamp"]

        # Skip already-processed commits
        if commit_hash in commit_files:
            continue

        # Classify files
        source_files: list[str] = []
        test_files: list[str] = []

        for filepath in commit["files"]:
            classification = classify_file(
                filepath, source_extensions, test_patterns
            )
            if classification == "source":
                source_files.append(filepath)
            elif classification == "test":
                test_files.append(filepath)

        # Skip commits with no source files (no co-occurrence signal)
        if not source_files:
            continue

        # Record the commit
        commit_files[commit_hash] = {
            "timestamp": timestamp,
            "source_files": source_files,
            "test_files": test_files,
        }

        # Index each file to this commit
        for filepath in source_files + test_files:
            if filepath not in file_commits:
                file_commits[filepath] = []
            file_commits[filepath].append({
                "commit": commit_hash,
                "timestamp": timestamp,
            })

        new_commits_count += 1
        if last_commit_hash is None:
            last_commit_hash = commit_hash

    # Determine the last commit hash
    if last_commit_hash is None and existing_graph is not None:
        last_commit_hash = existing_graph.get("metadata", {}).get(
            "last_commit"
        )

    now = datetime.now(timezone.utc).isoformat()

    return {
        "metadata": {
            "built_at": now,
            "last_commit": last_commit_hash,
            "total_commits_analyzed": total_analyzed + new_commits_count,
            "source_extensions": source_extensions,
            "test_patterns": test_patterns,
        },
        "file_commits": file_commits,
        "commit_files": commit_files,
    }


def run_git_log(
    repo_root: str | Path,
    max_history: int = 10000,
    since_commit: str | None = None,
) -> str:
    """Run git log to extract commit data.

    Args:
        repo_root: Path to the git repository root.
        max_history: Maximum number of commits to analyze.
        since_commit: Only include commits after this hash (exclusive).

    Returns:
        Raw git log output string.
    """
    cmd = [
        "git", "-C", str(repo_root),
        "log",
        f"--format=COMMIT %H %aI",
        "--name-only",
        f"-n{max_history}",
    ]

    if since_commit:
        cmd.append(f"{since_commit}..HEAD")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(f"git log failed: {result.stderr.strip()}")

    return result.stdout


def save_graph(graph: dict[str, Any], output_path: str | Path) -> None:
    """Save co-occurrence graph to a JSON file.

    Creates parent directories if they don't exist.

    Args:
        graph: Graph dict from build_co_occurrence_graph.
        output_path: Path to write the JSON file.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2) + "\n")


def load_graph(input_path: str | Path) -> dict[str, Any] | None:
    """Load co-occurrence graph from a JSON file.

    Args:
        input_path: Path to the JSON file.

    Returns:
        Parsed graph dict, or None if file doesn't exist.
    """
    path = Path(input_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
