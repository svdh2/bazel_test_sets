"""Source link resolution for structured log events.

Determines whether source code links should point to GitHub or use local
file paths, based on git remote URL and working tree state.
"""

from __future__ import annotations

import html
import re
import subprocess
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
        return None
    if commit_sha.endswith("-dirty"):
        return None

    github_url = _parse_github_remote()
    if github_url is None:
        return None

    if not _is_commit_on_remote(commit_sha):
        return None

    return f"{github_url}/blob/{commit_sha}"


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
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    url = result.stdout.strip()

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
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    if result.returncode != 0:
        return False

    return bool(result.stdout.strip())


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
