"""Tests for source link resolution and rendering."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import subprocess

from orchestrator.reporting.source_links import (
    _is_commit_on_remote,
    _parse_github_remote,
    build_source_link,
    render_source_link,
    resolve_source_link_base,
)


class TestParseGithubRemote:
    """Tests for _parse_github_remote."""

    def test_https_url(self):
        """HTTPS remote URL is parsed correctly."""
        result = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        with patch("subprocess.run", return_value=result):
            assert _parse_github_remote() == "https://github.com/owner/repo"

    def test_https_url_without_git_suffix(self):
        """HTTPS remote without .git suffix is parsed correctly."""
        result = MagicMock(returncode=0, stdout="https://github.com/owner/repo\n")
        with patch("subprocess.run", return_value=result):
            assert _parse_github_remote() == "https://github.com/owner/repo"

    def test_ssh_url(self):
        """SSH remote URL is parsed correctly."""
        result = MagicMock(returncode=0, stdout="git@github.com:owner/repo.git\n")
        with patch("subprocess.run", return_value=result):
            assert _parse_github_remote() == "https://github.com/owner/repo"

    def test_ssh_url_without_git_suffix(self):
        """SSH remote without .git suffix is parsed correctly."""
        result = MagicMock(returncode=0, stdout="git@github.com:owner/repo\n")
        with patch("subprocess.run", return_value=result):
            assert _parse_github_remote() == "https://github.com/owner/repo"

    def test_non_github_url_returns_none(self):
        """Non-GitHub remote returns None."""
        result = MagicMock(returncode=0, stdout="https://gitlab.com/owner/repo.git\n")
        with patch("subprocess.run", return_value=result):
            assert _parse_github_remote() is None

    def test_git_not_found_returns_none(self):
        """FileNotFoundError from subprocess returns None."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _parse_github_remote() is None

    def test_git_failure_returns_none(self):
        """Non-zero exit code returns None."""
        result = MagicMock(returncode=1, stdout="", stderr="not a repo")
        with patch("subprocess.run", return_value=result):
            assert _parse_github_remote() is None

    def test_timeout_returns_none(self):
        """Timeout returns None."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            assert _parse_github_remote() is None


class TestIsCommitOnRemote:
    """Tests for _is_commit_on_remote."""

    def test_commit_on_remote(self):
        """Returns True when commit is on a remote branch."""
        result = MagicMock(returncode=0, stdout="  origin/main\n")
        with patch("subprocess.run", return_value=result):
            assert _is_commit_on_remote("abc123") is True

    def test_commit_not_on_remote(self):
        """Returns False when output is empty."""
        result = MagicMock(returncode=0, stdout="\n")
        with patch("subprocess.run", return_value=result):
            assert _is_commit_on_remote("abc123") is False

    def test_git_failure(self):
        """Returns False on git error."""
        result = MagicMock(returncode=128, stdout="", stderr="error")
        with patch("subprocess.run", return_value=result):
            assert _is_commit_on_remote("abc123") is False


class TestResolveSourceLinkBase:
    """Tests for resolve_source_link_base."""

    def test_none_sha_returns_none(self):
        """None commit SHA returns None."""
        assert resolve_source_link_base(None) is None

    def test_dirty_sha_returns_none(self):
        """Dirty commit SHA returns None."""
        assert resolve_source_link_base("abc123-dirty") is None

    def test_clean_github_remote_on_remote(self):
        """Clean SHA with GitHub remote and commit on remote returns URL."""
        with patch(
            "orchestrator.reporting.source_links._parse_github_remote",
            return_value="https://github.com/owner/repo",
        ), patch(
            "orchestrator.reporting.source_links._is_commit_on_remote",
            return_value=True,
        ):
            result = resolve_source_link_base("abc123")
            assert result == "https://github.com/owner/repo/blob/abc123"

    def test_non_github_remote_returns_none(self):
        """Non-GitHub remote returns None."""
        with patch(
            "orchestrator.reporting.source_links._parse_github_remote",
            return_value=None,
        ):
            assert resolve_source_link_base("abc123") is None

    def test_commit_not_on_remote_returns_none(self):
        """Commit not on any remote branch returns None."""
        with patch(
            "orchestrator.reporting.source_links._parse_github_remote",
            return_value="https://github.com/owner/repo",
        ), patch(
            "orchestrator.reporting.source_links._is_commit_on_remote",
            return_value=False,
        ):
            assert resolve_source_link_base("abc123") is None


class TestBuildSourceLink:
    """Tests for build_source_link."""

    def test_github_link(self):
        """With source_link_base, returns a GitHub URL."""
        base = "https://github.com/owner/repo/blob/abc123"
        result = build_source_link(base, "src/main.py", 42)
        assert result == "https://github.com/owner/repo/blob/abc123/src/main.py#L42"

    def test_local_link(self):
        """Without source_link_base, returns path:line format."""
        result = build_source_link(None, "src/main.py", 42)
        assert result == "src/main.py:42"


class TestRenderSourceLink:
    """Tests for render_source_link."""

    def test_github_link_renders_anchor(self):
        """GitHub link renders as an <a> tag with target=_blank."""
        event = {"name": "x", "_file": "src/main.py", "_line": 42}
        base = "https://github.com/owner/repo/blob/abc123"
        result = render_source_link(event, base)
        assert '<a class="source-link"' in result
        assert 'target="_blank"' in result
        assert "src/main.py:42" in result
        assert "src/main.py#L42" in result

    def test_local_link_renders_span(self):
        """Local link renders as a <span>."""
        event = {"name": "x", "_file": "src/main.py", "_line": 42}
        result = render_source_link(event, None)
        assert '<span class="source-link">' in result
        assert "src/main.py:42" in result
        assert "<a " not in result

    def test_no_source_metadata_returns_empty(self):
        """Event without _file/_line returns empty string."""
        event = {"name": "x"}
        assert render_source_link(event, None) == ""

    def test_partial_source_metadata_returns_empty(self):
        """Event with only _file but no _line returns empty string."""
        event = {"name": "x", "_file": "src/main.py"}
        assert render_source_link(event, None) == ""

    def test_html_escaping(self):
        """File paths with special chars are escaped."""
        event = {"name": "x", "_file": "src/<script>.py", "_line": 1}
        result = render_source_link(event, None)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
