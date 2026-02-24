"""Unit tests for target hash computation via Bazel aquery.

Uses mocked subprocess output to test hash extraction logic without
requiring a real Bazel workspace or build.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.execution.target_hash import (
    _extract_hashes_from_aquery,
    _run_aquery,
    compute_target_hashes,
)


# ---------------------------------------------------------------------------
# Fixtures: sample aquery JSON output
# ---------------------------------------------------------------------------


def _make_aquery_json(
    targets: list[dict[str, Any]],
    actions: list[dict[str, Any]],
) -> str:
    """Build a minimal aquery --output=jsonproto JSON string."""
    return json.dumps({"targets": targets, "actions": actions})


def _simple_aquery_output() -> str:
    """Aquery output with two targets, each having distinct actions."""
    return _make_aquery_json(
        targets=[
            {"id": 1, "label": "//test:a"},
            {"id": 2, "label": "//test:b"},
        ],
        actions=[
            {"targetId": 1, "actionKey": "digest_a1"},
            {"targetId": 1, "actionKey": "digest_a2"},
            {"targetId": 2, "actionKey": "digest_b1"},
        ],
    )


def _single_target_output() -> str:
    """Aquery output with a single target and one action."""
    return _make_aquery_json(
        targets=[{"id": 1, "label": "//test:single"}],
        actions=[{"targetId": 1, "actionKey": "digest_single"}],
    )


def _empty_aquery_output() -> str:
    """Aquery output with no targets or actions."""
    return _make_aquery_json(targets=[], actions=[])


def _extra_targets_output() -> str:
    """Aquery output with more targets than requested (transitive deps)."""
    return _make_aquery_json(
        targets=[
            {"id": 1, "label": "//test:requested"},
            {"id": 2, "label": "//lib:transitive_dep"},
            {"id": 3, "label": "//test:other_requested"},
        ],
        actions=[
            {"targetId": 1, "actionKey": "digest_req1"},
            {"targetId": 2, "actionKey": "digest_trans"},
            {"targetId": 3, "actionKey": "digest_other1"},
        ],
    )


# ---------------------------------------------------------------------------
# Tests: _extract_hashes_from_aquery
# ---------------------------------------------------------------------------


class TestExtractHashes:
    """Tests for extracting per-target hashes from aquery JSON output."""

    def test_two_targets_get_distinct_hashes(self) -> None:
        aquery = json.loads(_simple_aquery_output())
        result = _extract_hashes_from_aquery(aquery, ["//test:a", "//test:b"])
        assert "//test:a" in result
        assert "//test:b" in result
        assert result["//test:a"] != result["//test:b"]

    def test_hash_is_hex_string(self) -> None:
        aquery = json.loads(_single_target_output())
        result = _extract_hashes_from_aquery(aquery, ["//test:single"])
        hash_val = result["//test:single"]
        assert len(hash_val) == 16  # truncated sha256
        assert all(c in "0123456789abcdef" for c in hash_val)

    def test_same_input_produces_same_hash(self) -> None:
        aquery = json.loads(_simple_aquery_output())
        result1 = _extract_hashes_from_aquery(aquery, ["//test:a"])
        result2 = _extract_hashes_from_aquery(aquery, ["//test:a"])
        assert result1["//test:a"] == result2["//test:a"]

    def test_only_requested_labels_returned(self) -> None:
        aquery = json.loads(_extra_targets_output())
        result = _extract_hashes_from_aquery(aquery, ["//test:requested"])
        assert "//test:requested" in result
        assert "//lib:transitive_dep" not in result
        assert "//test:other_requested" not in result

    def test_multiple_requested_with_extra_targets(self) -> None:
        aquery = json.loads(_extra_targets_output())
        result = _extract_hashes_from_aquery(
            aquery, ["//test:requested", "//test:other_requested"]
        )
        assert len(result) == 2
        assert "//test:requested" in result
        assert "//test:other_requested" in result

    def test_missing_label_omitted(self) -> None:
        aquery = json.loads(_simple_aquery_output())
        result = _extract_hashes_from_aquery(
            aquery, ["//test:a", "//test:nonexistent"]
        )
        assert "//test:a" in result
        assert "//test:nonexistent" not in result

    def test_empty_aquery_returns_empty(self) -> None:
        aquery = json.loads(_empty_aquery_output())
        result = _extract_hashes_from_aquery(aquery, ["//test:a"])
        assert result == {}

    def test_empty_labels_returns_empty(self) -> None:
        aquery = json.loads(_simple_aquery_output())
        result = _extract_hashes_from_aquery(aquery, [])
        assert result == {}

    def test_action_without_key_ignored(self) -> None:
        aquery = {
            "targets": [{"id": 1, "label": "//test:a"}],
            "actions": [
                {"targetId": 1, "actionKey": "real_digest"},
                {"targetId": 1, "actionKey": ""},  # empty key
                {"targetId": 1},  # missing key
            ],
        }
        result = _extract_hashes_from_aquery(aquery, ["//test:a"])
        assert "//test:a" in result

    def test_action_with_unknown_target_id_ignored(self) -> None:
        aquery = {
            "targets": [{"id": 1, "label": "//test:a"}],
            "actions": [
                {"targetId": 999, "actionKey": "orphan_digest"},
                {"targetId": 1, "actionKey": "real_digest"},
            ],
        }
        result = _extract_hashes_from_aquery(aquery, ["//test:a"])
        assert "//test:a" in result

    def test_hash_deterministic_regardless_of_action_order(self) -> None:
        """Hash should be the same regardless of action insertion order."""
        aquery_forward = {
            "targets": [{"id": 1, "label": "//test:a"}],
            "actions": [
                {"targetId": 1, "actionKey": "digest_1"},
                {"targetId": 1, "actionKey": "digest_2"},
                {"targetId": 1, "actionKey": "digest_3"},
            ],
        }
        aquery_reverse = {
            "targets": [{"id": 1, "label": "//test:a"}],
            "actions": [
                {"targetId": 1, "actionKey": "digest_3"},
                {"targetId": 1, "actionKey": "digest_1"},
                {"targetId": 1, "actionKey": "digest_2"},
            ],
        }
        result1 = _extract_hashes_from_aquery(aquery_forward, ["//test:a"])
        result2 = _extract_hashes_from_aquery(aquery_reverse, ["//test:a"])
        assert result1["//test:a"] == result2["//test:a"]

    def test_different_digests_produce_different_hashes(self) -> None:
        aquery1 = {
            "targets": [{"id": 1, "label": "//test:a"}],
            "actions": [{"targetId": 1, "actionKey": "digest_v1"}],
        }
        aquery2 = {
            "targets": [{"id": 1, "label": "//test:a"}],
            "actions": [{"targetId": 1, "actionKey": "digest_v2"}],
        }
        result1 = _extract_hashes_from_aquery(aquery1, ["//test:a"])
        result2 = _extract_hashes_from_aquery(aquery2, ["//test:a"])
        assert result1["//test:a"] != result2["//test:a"]


# ---------------------------------------------------------------------------
# Tests: _run_aquery
# ---------------------------------------------------------------------------


class TestRunAquery:
    """Tests for running bazel aquery subprocess."""

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_successful_aquery(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_simple_aquery_output(), stderr=""
        )
        result = _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        assert result is not None
        assert "targets" in result
        assert "actions" in result

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_single_label_query_expression(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        call_args = mock_run.call_args[0][0]
        # Single label should not have '+' join
        assert "//test:a" in call_args
        assert "+" not in " ".join(call_args)

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_multiple_labels_joined(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        _run_aquery(
            ["//test:a", "//test:b"], workspace_dir="/fake/ws"
        )
        call_args = mock_run.call_args[0][0]
        # Multiple labels joined with ' + '
        query_arg = call_args[-1]
        assert "//test:a" in query_arg
        assert "//test:b" in query_arg

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_bazel_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("bazel not found")
        result = _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        assert result is None

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_timeout_returns_none(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="bazel", timeout=60)
        result = _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        assert result is None

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_nonzero_exit_returns_none(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        result = _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        assert result is None

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_invalid_json_returns_none(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json{{{", stderr=""
        )
        result = _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        assert result is None

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_empty_stdout_returns_empty_dict(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="  \n  ", stderr=""
        )
        result = _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        assert result == {}

    def test_no_workspace_dir_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = _run_aquery(["//test:a"], workspace_dir=None)
            assert result is None

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_workspace_dir_from_env(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        with patch.dict(
            "os.environ", {"BUILD_WORKSPACE_DIRECTORY": "/env/ws"}
        ):
            _run_aquery(["//test:a"], workspace_dir=None)
        assert mock_run.call_args[1]["cwd"] == "/env/ws"

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_explicit_workspace_dir_overrides_env(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        with patch.dict(
            "os.environ", {"BUILD_WORKSPACE_DIRECTORY": "/env/ws"}
        ):
            _run_aquery(["//test:a"], workspace_dir="/explicit/ws")
        assert mock_run.call_args[1]["cwd"] == "/explicit/ws"

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_timeout_passed_to_subprocess(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        _run_aquery(["//test:a"], workspace_dir="/fake/ws", timeout=30)
        assert mock_run.call_args[1]["timeout"] == 30

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_uses_jsonproto_output_format(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        _run_aquery(["//test:a"], workspace_dir="/fake/ws")
        call_args = mock_run.call_args[0][0]
        assert "--output=jsonproto" in call_args


# ---------------------------------------------------------------------------
# Tests: compute_target_hashes (integration of run + extract)
# ---------------------------------------------------------------------------


class TestComputeTargetHashes:
    """Tests for the top-level compute_target_hashes function."""

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_returns_hashes_for_found_targets(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_simple_aquery_output(), stderr=""
        )
        result = compute_target_hashes(
            ["//test:a", "//test:b"], workspace_dir="/fake/ws"
        )
        assert "//test:a" in result
        assert "//test:b" in result

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_missing_target_omitted(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_simple_aquery_output(), stderr=""
        )
        result = compute_target_hashes(
            ["//test:a", "//test:missing"], workspace_dir="/fake/ws"
        )
        assert "//test:a" in result
        assert "//test:missing" not in result

    def test_empty_labels_returns_empty(self) -> None:
        result = compute_target_hashes([], workspace_dir="/fake/ws")
        assert result == {}

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_aquery_failure_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        result = compute_target_hashes(
            ["//test:a"], workspace_dir="/fake/ws"
        )
        assert result == {}

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_bazel_not_found_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError("bazel not found")
        result = compute_target_hashes(
            ["//test:a"], workspace_dir="/fake/ws"
        )
        assert result == {}

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_timeout_returns_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="bazel", timeout=60)
        result = compute_target_hashes(
            ["//test:a"], workspace_dir="/fake/ws"
        )
        assert result == {}

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_consistent_hashes_across_calls(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_simple_aquery_output(), stderr=""
        )
        result1 = compute_target_hashes(
            ["//test:a"], workspace_dir="/fake/ws"
        )
        result2 = compute_target_hashes(
            ["//test:a"], workspace_dir="/fake/ws"
        )
        assert result1["//test:a"] == result2["//test:a"]

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_custom_timeout(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=_simple_aquery_output(), stderr=""
        )
        compute_target_hashes(
            ["//test:a"], workspace_dir="/fake/ws", timeout=10
        )
        assert mock_run.call_args[1]["timeout"] == 10

    def test_no_workspace_returns_empty(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = compute_target_hashes(
                ["//test:a"], workspace_dir=None
            )
            assert result == {}

    @patch("orchestrator.execution.target_hash.subprocess.run")
    def test_only_requested_labels_in_result(
        self, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=_extra_targets_output(),
            stderr="",
        )
        result = compute_target_hashes(
            ["//test:requested"], workspace_dir="/fake/ws"
        )
        assert "//test:requested" in result
        assert "//lib:transitive_dep" not in result
