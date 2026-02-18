"""Source link resolution for structured log events.

Determines whether source code links should point to GitHub or use local
file paths, based on git remote URL and working tree state.
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from typing import Any


def resolve_source_link_base(
    commit_sha: str | None,
) -> str | None:
    """Determine the base URL for source code links.

    Returns a GitHub blob URL prefix when:
    1. ``commit_sha`` is available and not dirty,
    2. the ``origin`` remote points to GitHub, and
    3. the commit exists on at least one remote branch.

    Otherwise returns ``None``, indicating local file paths should be used.

    Args:
        commit_sha: The HEAD commit SHA (may end with ``-dirty``).

    Returns:
        GitHub URL prefix like
        ``https://github.com/owner/repo/blob/<sha>``
        or ``None`` for local-path mode.
    """
    if commit_sha is None:
        print("source links: no commit_sha provided", file=sys.stderr)
        return None
    if commit_sha.endswith("-dirty"):
        print(f"source links: commit_sha is dirty: {commit_sha}", file=sys.stderr)
        return None

    github_url = _parse_github_remote()
    if github_url is None:
        print("source links: could not parse GitHub remote from origin", file=sys.stderr)
        return None

    if not _is_commit_on_remote(commit_sha):
        print(f"source links: commit {commit_sha} not found on any remote branch", file=sys.stderr)
        return None

    prefix = _cwd_repo_prefix()
    base = f"{github_url}/blob/{commit_sha}"
    if prefix:
        base = f"{base}/{prefix}"
    print(f"source links: resolved base URL: {base}", file=sys.stderr)
    return base


def _cwd_repo_prefix() -> str:
    """Return the Bazel workspace path relative to the git repo root.

    Compares ``BUILD_WORKSPACE_DIRECTORY`` (set by ``bazel run``) against
    ``git rev-parse --show-toplevel`` to compute the prefix.  For example,
    when the Bazel workspace lives in ``<repo>/examples/``, returns
    ``examples``.

    Returns an empty string when already at the repo root, outside Bazel,
    or if git is unavailable.
    """
    import os

    workspace_dir = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace_dir:
        return ""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=workspace_dir,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""

    repo_root = result.stdout.strip()
    prefix = os.path.relpath(workspace_dir, repo_root)
    if prefix == ".":
        return ""
    return prefix


def _parse_github_remote() -> str | None:
    """Extract GitHub HTTPS URL from git remote ``origin``.

    Handles both HTTPS and SSH remote URL formats:

    * ``https://github.com/owner/repo.git``
    * ``git@github.com:owner/repo.git``

    Returns:
        ``https://github.com/owner/repo`` or ``None``.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"source links: git remote get-url failed: {exc}", file=sys.stderr)
        return None

    if result.returncode != 0:
        print(f"source links: git remote get-url returned {result.returncode}: {result.stderr.strip()}", file=sys.stderr)
        return None

    url = result.stdout.strip()
    print(f"source links: origin remote URL: {url}", file=sys.stderr)

    # HTTPS format
    match = re.match(
        r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?$", url
    )
    if match:
        return f"https://github.com/{match.group(1)}/{match.group(2)}"

    # SSH format
    match = re.match(
        r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", url
    )
    if match:
        return f"https://github.com/{match.group(1)}/{match.group(2)}"

    print("source links: origin URL did not match GitHub patterns", file=sys.stderr)
    return None


def _is_commit_on_remote(commit_sha: str) -> bool:
    """Check whether *commit_sha* exists on at least one remote branch.

    Uses ``git branch -r --contains <sha>`` which lists remote-tracking
    branches that contain the given commit.
    """
    try:
        result = subprocess.run(
            ["git", "branch", "-r", "--contains", commit_sha],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"source links: git branch -r --contains failed: {exc}", file=sys.stderr)
        return False

    if result.returncode != 0:
        print(f"source links: git branch -r --contains returned {result.returncode}: {result.stderr.strip()}", file=sys.stderr)
        return False

    branches = result.stdout.strip()
    if branches:
        print(f"source links: commit on remote branches: {branches}", file=sys.stderr)
        return True
    print("source links: commit not on any remote branch", file=sys.stderr)
    return False


def build_source_link(
    source_link_base: str | None,
    file_path: str,
    line_number: int,
) -> str:
    """Build a source code link for a specific file and line.

    Args:
        source_link_base: GitHub blob URL prefix, or ``None`` for local
            paths.
        file_path: Repo-relative file path.
        line_number: Line number.

    Returns:
        Full URL string (GitHub) or ``path:line`` string (local).
    """
    if source_link_base is not None:
        return f"{source_link_base}/{file_path}#L{line_number}"
    return f"{file_path}:{line_number}"


def render_source_link(
    event: dict[str, Any],
    source_link_base: str | None,
) -> str:
    """Render an HTML source code link for an event with source metadata.

    Returns an empty string if *event* has no ``_file`` / ``_line`` keys.

    Args:
        event: Parsed event dict (feature, measurement, assertion, or
            error) that may contain ``_file`` and ``_line`` metadata.
        source_link_base: GitHub blob URL prefix, or ``None`` for local
            path display.

    Returns:
        HTML fragment (``<a>`` for GitHub, ``<span>`` for local) or ``""``.
    """
    file_path = event.get("_file")
    line_num = event.get("_line")
    if file_path is None or line_num is None:
        return ""

    href = build_source_link(source_link_base, file_path, line_num)
    display = f"{html.escape(str(file_path))}:{line_num}"

    if source_link_base is not None:
        return (
            f' <a class="source-link" href="{html.escape(href)}" '
            f'target="_blank">{display}</a>'
        )
    return f' <span class="source-link">{display}</span>'
