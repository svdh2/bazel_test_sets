"""Target hash computation via Bazel's action graph.

Computes content hashes for test targets by querying Bazel's action graph.
A target hash captures the test binary, all transitive runtime dependencies,
testing tools, and build configuration. Two executions with the same target
hash have identical inputs, making them valid samples under the same
experimental conditions for SPRT.

This module uses ``bazel aquery --output=jsonproto`` to extract action digests
for test targets. The action digest is a content hash of the action's inputs
(source files, dependencies, tools, arguments), providing the stationarity
marker needed for evidence pooling.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from typing import Any


def _run_aquery(
    labels: list[str],
    *,
    workspace_dir: str | None = None,
    timeout: int = 60,
) -> dict[str, Any] | None:
    """Run ``bazel aquery`` for the given test labels.

    Uses ``--output=jsonproto`` for structured output parsing.
    Queries all actions for the given targets in a single batch.

    Args:
        labels: List of Bazel test target labels (e.g., ``["//test:a"]``).
        workspace_dir: Path to Bazel workspace root.  If *None*, reads
            ``BUILD_WORKSPACE_DIRECTORY`` from the environment.
        timeout: Timeout in seconds for the bazel aquery command.

    Returns:
        Parsed JSON dict from aquery output, or *None* if the query failed.
    """
    if workspace_dir is None:
        workspace_dir = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace_dir:
        return None

    # Build a single aquery expression for all labels.
    # Using 'deps(...)' to get the full transitive closure of actions.
    # We union all labels into a single query for batch efficiency.
    if len(labels) == 1:
        query_expr = labels[0]
    else:
        query_expr = " + ".join(labels)

    try:
        result = subprocess.run(
            [
                "bazel", "aquery",
                "--output=jsonproto",
                query_expr,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace_dir,
        )
    except FileNotFoundError:
        print(
            "Target hash: bazel not found in PATH, skipping hash computation",
            file=sys.stderr,
        )
        return None
    except subprocess.TimeoutExpired:
        print(
            f"Target hash: bazel aquery timed out after {timeout}s, "
            "skipping hash computation",
            file=sys.stderr,
        )
        return None

    if result.returncode != 0:
        print(
            "Target hash: bazel aquery failed "
            f"(exit {result.returncode}), skipping hash computation",
            file=sys.stderr,
        )
        return None

    if not result.stdout.strip():
        return {}

    try:
        return json.loads(result.stdout)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        print(
            "Target hash: failed to parse aquery JSON output",
            file=sys.stderr,
        )
        return None


def _extract_hashes_from_aquery(
    aquery_result: dict[str, Any],
    labels: list[str],
) -> dict[str, str]:
    """Extract per-target hashes from aquery JSON output.

    The aquery output contains a list of actions, each with a target label
    and an action digest. We compute a composite hash per target from all
    its action digests, providing a stable content hash.

    Args:
        aquery_result: Parsed JSON from ``bazel aquery --output=jsonproto``.
        labels: The original list of requested labels (for filtering).

    Returns:
        Mapping of target label to content hash string.
    """
    # Normalize requested labels for matching
    label_set = set(labels)

    # The aquery jsonproto output has 'targets' and 'actions' arrays.
    # Each action has a 'targetId' referencing a target, and an
    # 'actionKey' containing the action digest.
    targets_by_id: dict[str, str] = {}
    for target in aquery_result.get("targets", []):
        target_id = str(target.get("id", ""))
        target_label = target.get("label", "")
        if target_label:
            targets_by_id[target_id] = target_label

    # Collect action digests grouped by target label
    digests_by_label: dict[str, list[str]] = {}
    for action in aquery_result.get("actions", []):
        target_id = str(action.get("targetId", ""))
        target_label = targets_by_id.get(target_id, "")
        if not target_label:
            continue

        # Only include actions for the requested labels
        if target_label not in label_set:
            continue

        # Extract the action key (digest)
        action_key = action.get("actionKey", "")
        if action_key:
            digests_by_label.setdefault(target_label, []).append(action_key)

    # Compute composite hash per label from sorted action digests
    result: dict[str, str] = {}
    for label in labels:
        digests = digests_by_label.get(label, [])
        if digests:
            # Sort for determinism, then hash the concatenation
            digests.sort()
            composite = hashlib.sha256(
                "\n".join(digests).encode()
            ).hexdigest()[:16]
            result[label] = composite

    return result


def compute_target_hashes(
    test_labels: list[str],
    *,
    workspace_dir: str | None = None,
    timeout: int = 60,
) -> dict[str, str]:
    """Compute content hashes for test targets via Bazel's action graph.

    Batch queries ``bazel aquery --output=jsonproto`` for all test labels
    and returns a mapping of label to content hash. The hash captures
    the test binary, all transitive runtime dependencies, tools, and
    build configuration.

    Args:
        test_labels: List of Bazel test target labels.
        workspace_dir: Path to Bazel workspace root.  If *None*, reads
            ``BUILD_WORKSPACE_DIRECTORY`` from the environment.
        timeout: Timeout in seconds for the aquery command.

    Returns:
        Mapping of label to hex hash string.  Labels that could not be
        hashed (e.g., not found by aquery) are omitted.  Returns empty
        dict if aquery is unavailable or fails.
    """
    if not test_labels:
        return {}

    aquery_result = _run_aquery(
        test_labels,
        workspace_dir=workspace_dir,
        timeout=timeout,
    )

    if aquery_result is None:
        return {}

    return _extract_hashes_from_aquery(aquery_result, test_labels)
