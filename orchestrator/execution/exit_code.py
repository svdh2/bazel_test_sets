"""Lifecycle-aware exit code computation.

Determines the orchestrator exit code by combining each test's SPRT
classification (true_pass, true_fail, flake, undecided) with its
lifecycle state from the status file (stable, flaky, burning_in, new,
disabled).

The behaviour differs by stage mode:

**Regression mode** (PR / merge gate):
    Only ``stable`` tests with ``true_fail`` or ``undecided``
    classification block.  Tests in ``flaky``, ``burning_in``, or
    ``new`` state are non-blocking regardless of classification.
    ``stable + flake`` is also non-blocking (a warning, not a block).

**Converge / max modes** (post-merge / release):
    ``true_fail``, ``flake``, and ``undecided`` all block, regardless
    of lifecycle state.  Only ``true_pass`` is non-blocking.

Tests not present in the status file default to the ``stable``
lifecycle state.

Lifecycle x Classification Matrix (regression mode)::

    +--------------+------------+------------+-------+-----------+
    | lifecycle    | true_pass  | true_fail  | flake | undecided |
    +--------------+------------+------------+-------+-----------+
    | stable       | OK         | BLOCK      | OK    | BLOCK     |
    | flaky        | OK         | OK         | OK    | OK        |
    | burning_in   | OK         | OK         | OK    | OK        |
    | new          | OK         | OK         | OK    | OK        |
    | disabled     | OK         | OK         | OK    | OK        |
    +--------------+------------+------------+-------+-----------+

Lifecycle x Classification Matrix (converge / max mode)::

    +--------------+------------+------------+-------+-----------+
    | lifecycle    | true_pass  | true_fail  | flake | undecided |
    +--------------+------------+------------+-------+-----------+
    | (any)        | OK         | BLOCK      | BLOCK | BLOCK     |
    +--------------+------------+------------+-------+-----------+
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Lifecycle states that are non-blocking in regression mode.
_NON_BLOCKING_STATES = frozenset({"flaky", "burning_in", "new", "disabled"})

# Classifications that are blocking for *stable* tests in regression mode.
_REGRESSION_BLOCKING_CLASSIFICATIONS = frozenset({"true_fail", "undecided"})

# Classifications that are blocking in converge/max modes (all states).
_CONVERGE_BLOCKING_CLASSIFICATIONS = frozenset({
    "true_fail", "flake", "undecided",
})

# Stage modes that use lifecycle-aware exit codes.
_LIFECYCLE_AWARE_MODES = frozenset({"regression"})


@dataclass
class ExitCodeSummary:
    """Summary of blocking / non-blocking test counts."""

    exit_code: int
    blocking_tests: list[str]
    non_blocking_tests: list[str]
    warnings: list[str]


def classify_test_blocking(
    classification: str,
    lifecycle_state: str,
    stage_mode: str,
) -> bool:
    """Determine whether a single test should block (cause exit code 1).

    Args:
        classification: SPRT classification -- one of ``true_pass``,
            ``true_fail``, ``flake``, ``undecided``.
        lifecycle_state: Lifecycle state from the status file -- one of
            ``stable``, ``flaky``, ``burning_in``, ``new``, ``disabled``.
        stage_mode: The effort / stage mode -- ``regression``,
            ``converge``, or ``max``.

    Returns:
        ``True`` if this test should cause exit code 1.
    """
    if stage_mode in _LIFECYCLE_AWARE_MODES:
        # Regression mode: lifecycle-aware
        if lifecycle_state in _NON_BLOCKING_STATES:
            return False
        # stable: only true_fail and undecided block
        return classification in _REGRESSION_BLOCKING_CLASSIFICATIONS
    else:
        # Converge / max modes: no lifecycle awareness
        return classification in _CONVERGE_BLOCKING_CLASSIFICATIONS


def compute_exit_code(
    classifications: dict[str, Any],
    status_file: Any | None,
    mode: str,
) -> ExitCodeSummary:
    """Compute the exit code from SPRT classifications and lifecycle state.

    For each classified test, looks up its lifecycle state from the status
    file and determines whether it should block.  Tests not in the status
    file default to ``stable``.

    Args:
        classifications: Mapping of test name to an object with a
            ``.classification`` attribute (e.g. ``EffortClassification``).
        status_file: A ``StatusFile`` instance, or ``None``.  When
            ``None``, all tests default to ``stable``.
        mode: The effort / stage mode (``regression``, ``converge``,
            ``max``).

    Returns:
        ``ExitCodeSummary`` with exit code, blocking/non-blocking lists,
        and warnings.
    """
    blocking: list[str] = []
    non_blocking: list[str] = []
    warnings: list[str] = []

    for test_name, cls_obj in classifications.items():
        classification = cls_obj.classification

        # Look up lifecycle state
        if status_file is not None:
            state = status_file.get_test_state(test_name)
            if state is None:
                state = "stable"
        else:
            state = "stable"

        is_blocking = classify_test_blocking(classification, state, mode)

        if is_blocking:
            blocking.append(test_name)
        else:
            non_blocking.append(test_name)
            # Emit warning for stable+flake in regression mode
            if (
                mode in _LIFECYCLE_AWARE_MODES
                and state == "stable"
                and classification == "flake"
            ):
                warnings.append(
                    f"{test_name}: stable test classified as flake "
                    f"(non-blocking in {mode} mode)"
                )

    exit_code = 1 if blocking else 0
    return ExitCodeSummary(
        exit_code=exit_code,
        blocking_tests=blocking,
        non_blocking_tests=non_blocking,
        warnings=warnings,
    )
