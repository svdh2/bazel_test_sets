"""Exhaustive tests for lifecycle-aware exit code computation.

Tests cover every cell of the lifecycle x classification matrix for
both regression mode and converge/max modes.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from orchestrator.execution.exit_code import (
    ExitCodeSummary,
    classify_test_blocking,
    compute_exit_code,
)
from orchestrator.lifecycle.status import StatusFile


@dataclass
class _MockClassification:
    """Lightweight stand-in for EffortClassification in tests."""

    classification: str


# ---------------------------------------------------------------------------
# classify_test_blocking -- regression mode (lifecycle-aware)
# ---------------------------------------------------------------------------


class TestClassifyBlockingRegressionStable:
    """Regression mode blocking for tests in 'stable' state."""

    def test_stable_true_pass_not_blocking(self):
        assert classify_test_blocking("true_pass", "stable", "regression") is False

    def test_stable_true_fail_blocking(self):
        assert classify_test_blocking("true_fail", "stable", "regression") is True

    def test_stable_flake_not_blocking(self):
        assert classify_test_blocking("flake", "stable", "regression") is False

    def test_stable_undecided_blocking(self):
        assert classify_test_blocking("undecided", "stable", "regression") is True


class TestClassifyBlockingRegressionFlaky:
    """Regression mode: 'flaky' tests are non-blocking regardless."""

    def test_flaky_true_pass(self):
        assert classify_test_blocking("true_pass", "flaky", "regression") is False

    def test_flaky_true_fail(self):
        assert classify_test_blocking("true_fail", "flaky", "regression") is False

    def test_flaky_flake(self):
        assert classify_test_blocking("flake", "flaky", "regression") is False

    def test_flaky_undecided(self):
        assert classify_test_blocking("undecided", "flaky", "regression") is False


class TestClassifyBlockingRegressionBurningIn:
    """Regression mode: 'burning_in' tests are non-blocking regardless."""

    def test_burning_in_true_pass(self):
        assert classify_test_blocking("true_pass", "burning_in", "regression") is False

    def test_burning_in_true_fail(self):
        assert classify_test_blocking("true_fail", "burning_in", "regression") is False

    def test_burning_in_flake(self):
        assert classify_test_blocking("flake", "burning_in", "regression") is False

    def test_burning_in_undecided(self):
        assert classify_test_blocking("undecided", "burning_in", "regression") is False


class TestClassifyBlockingRegressionNew:
    """Regression mode: 'new' tests are non-blocking regardless."""

    def test_new_true_pass(self):
        assert classify_test_blocking("true_pass", "new", "regression") is False

    def test_new_true_fail(self):
        assert classify_test_blocking("true_fail", "new", "regression") is False

    def test_new_flake(self):
        assert classify_test_blocking("flake", "new", "regression") is False

    def test_new_undecided(self):
        assert classify_test_blocking("undecided", "new", "regression") is False


class TestClassifyBlockingRegressionDisabled:
    """Regression mode: 'disabled' tests are non-blocking regardless."""

    def test_disabled_true_pass(self):
        assert classify_test_blocking("true_pass", "disabled", "regression") is False

    def test_disabled_true_fail(self):
        assert classify_test_blocking("true_fail", "disabled", "regression") is False

    def test_disabled_flake(self):
        assert classify_test_blocking("flake", "disabled", "regression") is False

    def test_disabled_undecided(self):
        assert classify_test_blocking("undecided", "disabled", "regression") is False


# ---------------------------------------------------------------------------
# classify_test_blocking -- converge mode (no lifecycle awareness)
# ---------------------------------------------------------------------------


class TestClassifyBlockingConverge:
    """Converge mode: true_fail, flake, undecided all block."""

    def test_true_pass_not_blocking(self):
        assert classify_test_blocking("true_pass", "stable", "converge") is False

    def test_true_fail_blocking(self):
        assert classify_test_blocking("true_fail", "stable", "converge") is True

    def test_flake_blocking(self):
        assert classify_test_blocking("flake", "stable", "converge") is True

    def test_undecided_blocking(self):
        assert classify_test_blocking("undecided", "stable", "converge") is True

    def test_flaky_true_fail_still_blocking(self):
        """In converge mode, flaky lifecycle state doesn't help."""
        assert classify_test_blocking("true_fail", "flaky", "converge") is True

    def test_burning_in_flake_still_blocking(self):
        """In converge mode, burning_in lifecycle state doesn't help."""
        assert classify_test_blocking("flake", "burning_in", "converge") is True

    def test_new_undecided_still_blocking(self):
        """In converge mode, new lifecycle state doesn't help."""
        assert classify_test_blocking("undecided", "new", "converge") is True


# ---------------------------------------------------------------------------
# classify_test_blocking -- max mode (same as converge)
# ---------------------------------------------------------------------------


class TestClassifyBlockingMax:
    """Max mode behaves like converge."""

    def test_true_pass_not_blocking(self):
        assert classify_test_blocking("true_pass", "stable", "max") is False

    def test_true_fail_blocking(self):
        assert classify_test_blocking("true_fail", "stable", "max") is True

    def test_flake_blocking(self):
        assert classify_test_blocking("flake", "stable", "max") is True

    def test_undecided_blocking(self):
        assert classify_test_blocking("undecided", "stable", "max") is True

    def test_flaky_true_fail_still_blocking(self):
        assert classify_test_blocking("true_fail", "flaky", "max") is True


# ---------------------------------------------------------------------------
# compute_exit_code -- integration tests
# ---------------------------------------------------------------------------


class TestComputeExitCodeRegression:
    """compute_exit_code in regression mode."""

    def test_all_true_pass_exit_0(self):
        """All tests pass -> exit 0."""
        classifications = {
            "//test:a": _MockClassification("true_pass"),
            "//test:b": _MockClassification("true_pass"),
        }
        summary = compute_exit_code(classifications, None, "regression")
        assert summary.exit_code == 0
        assert summary.blocking_tests == []

    def test_stable_true_fail_exit_1(self):
        """Stable test with true_fail -> exit 1."""
        classifications = {
            "//test:a": _MockClassification("true_fail"),
        }
        summary = compute_exit_code(classifications, None, "regression")
        assert summary.exit_code == 1
        assert summary.blocking_tests == ["//test:a"]

    def test_flaky_true_fail_exit_0(self):
        """Flaky test with true_fail -> exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "flaky")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("true_fail"),
            }
            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 0
            assert summary.non_blocking_tests == ["//test:a"]

    def test_mixed_stable_and_flaky(self):
        """Stable failure blocks; flaky failure does not."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            sf.set_test_state("//test:b", "flaky")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("true_fail"),
                "//test:b": _MockClassification("true_fail"),
            }
            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 1
            assert summary.blocking_tests == ["//test:a"]
            assert summary.non_blocking_tests == ["//test:b"]

    def test_stable_flake_non_blocking_with_warning(self):
        """Stable + flake is non-blocking but generates a warning."""
        classifications = {
            "//test:a": _MockClassification("flake"),
        }
        summary = compute_exit_code(classifications, None, "regression")
        assert summary.exit_code == 0
        assert len(summary.warnings) == 1
        assert "stable test classified as flake" in summary.warnings[0]

    def test_burning_in_undecided_non_blocking(self):
        """burning_in + undecided is non-blocking in regression mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "burning_in")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("undecided"),
            }
            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 0

    def test_unknown_test_defaults_to_stable(self):
        """Test not in status file defaults to stable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.save()

            classifications = {
                "//test:unknown": _MockClassification("true_fail"),
            }
            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 1
            assert summary.blocking_tests == ["//test:unknown"]

    def test_no_status_file_all_default_stable(self):
        """No status file means all tests default to stable."""
        classifications = {
            "//test:a": _MockClassification("true_fail"),
        }
        summary = compute_exit_code(classifications, None, "regression")
        assert summary.exit_code == 1

    def test_empty_classifications_exit_0(self):
        """No tests -> exit 0."""
        summary = compute_exit_code({}, None, "regression")
        assert summary.exit_code == 0
        assert summary.blocking_tests == []
        assert summary.non_blocking_tests == []

    def test_all_non_blocking_exit_0(self):
        """All tests non-blocking -> exit 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "flaky")
            sf.set_test_state("//test:b", "new")
            sf.set_test_state("//test:c", "burning_in")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("true_fail"),
                "//test:b": _MockClassification("undecided"),
                "//test:c": _MockClassification("flake"),
            }
            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 0
            assert len(summary.non_blocking_tests) == 3

    def test_stable_undecided_blocking(self):
        """stable + undecided is blocking in regression mode."""
        classifications = {
            "//test:a": _MockClassification("undecided"),
        }
        summary = compute_exit_code(classifications, None, "regression")
        assert summary.exit_code == 1
        assert summary.blocking_tests == ["//test:a"]


class TestComputeExitCodeConverge:
    """compute_exit_code in converge mode."""

    def test_true_fail_exit_1(self):
        """true_fail blocks in converge mode."""
        classifications = {
            "//test:a": _MockClassification("true_fail"),
        }
        summary = compute_exit_code(classifications, None, "converge")
        assert summary.exit_code == 1

    def test_flake_exit_1(self):
        """flake blocks in converge mode."""
        classifications = {
            "//test:a": _MockClassification("flake"),
        }
        summary = compute_exit_code(classifications, None, "converge")
        assert summary.exit_code == 1

    def test_undecided_exit_1(self):
        """undecided blocks in converge mode."""
        classifications = {
            "//test:a": _MockClassification("undecided"),
        }
        summary = compute_exit_code(classifications, None, "converge")
        assert summary.exit_code == 1

    def test_all_true_pass_exit_0(self):
        """All true_pass -> exit 0."""
        classifications = {
            "//test:a": _MockClassification("true_pass"),
        }
        summary = compute_exit_code(classifications, None, "converge")
        assert summary.exit_code == 0

    def test_flaky_lifecycle_does_not_help(self):
        """In converge mode, flaky lifecycle does not make test non-blocking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "flaky")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("true_fail"),
            }
            summary = compute_exit_code(classifications, sf, "converge")
            assert summary.exit_code == 1

    def test_no_warnings_in_converge(self):
        """No flake warnings in converge mode."""
        classifications = {
            "//test:a": _MockClassification("flake"),
        }
        summary = compute_exit_code(classifications, None, "converge")
        assert summary.warnings == []


class TestComputeExitCodeMax:
    """compute_exit_code in max mode behaves like converge."""

    def test_true_fail_exit_1(self):
        classifications = {
            "//test:a": _MockClassification("true_fail"),
        }
        summary = compute_exit_code(classifications, None, "max")
        assert summary.exit_code == 1

    def test_flake_exit_1(self):
        classifications = {
            "//test:a": _MockClassification("flake"),
        }
        summary = compute_exit_code(classifications, None, "max")
        assert summary.exit_code == 1

    def test_true_pass_exit_0(self):
        classifications = {
            "//test:a": _MockClassification("true_pass"),
        }
        summary = compute_exit_code(classifications, None, "max")
        assert summary.exit_code == 0


# ---------------------------------------------------------------------------
# Exhaustive matrix test (parametrized)
# ---------------------------------------------------------------------------


_ALL_CLASSIFICATIONS = ["true_pass", "true_fail", "flake", "undecided"]
_ALL_STATES = ["stable", "flaky", "burning_in", "new", "disabled"]
_REGRESSION_EXPECTED = {
    # (classification, state) -> is_blocking
    ("true_pass", "stable"): False,
    ("true_fail", "stable"): True,
    ("flake", "stable"): False,
    ("undecided", "stable"): True,
    ("true_pass", "flaky"): False,
    ("true_fail", "flaky"): False,
    ("flake", "flaky"): False,
    ("undecided", "flaky"): False,
    ("true_pass", "burning_in"): False,
    ("true_fail", "burning_in"): False,
    ("flake", "burning_in"): False,
    ("undecided", "burning_in"): False,
    ("true_pass", "new"): False,
    ("true_fail", "new"): False,
    ("flake", "new"): False,
    ("undecided", "new"): False,
    ("true_pass", "disabled"): False,
    ("true_fail", "disabled"): False,
    ("flake", "disabled"): False,
    ("undecided", "disabled"): False,
}


class TestExhaustiveRegressionMatrix:
    """Parametrized test of every (classification, state) pair in regression mode."""

    @pytest.mark.parametrize(
        "classification,state",
        [
            (c, s)
            for c in _ALL_CLASSIFICATIONS
            for s in _ALL_STATES
        ],
    )
    def test_regression_matrix(self, classification: str, state: str):
        expected = _REGRESSION_EXPECTED[(classification, state)]
        actual = classify_test_blocking(classification, state, "regression")
        assert actual is expected, (
            f"classify_test_blocking({classification!r}, {state!r}, 'regression') "
            f"= {actual}, expected {expected}"
        )


_CONVERGE_EXPECTED = {
    "true_pass": False,
    "true_fail": True,
    "flake": True,
    "undecided": True,
}


class TestExhaustiveConvergeMatrix:
    """Parametrized test: converge mode ignores lifecycle state."""

    @pytest.mark.parametrize(
        "classification,state",
        [
            (c, s)
            for c in _ALL_CLASSIFICATIONS
            for s in _ALL_STATES
        ],
    )
    def test_converge_matrix(self, classification: str, state: str):
        expected = _CONVERGE_EXPECTED[classification]
        actual = classify_test_blocking(classification, state, "converge")
        assert actual is expected, (
            f"classify_test_blocking({classification!r}, {state!r}, 'converge') "
            f"= {actual}, expected {expected}"
        )


class TestExhaustiveMaxMatrix:
    """Parametrized test: max mode behaves like converge."""

    @pytest.mark.parametrize(
        "classification,state",
        [
            (c, s)
            for c in _ALL_CLASSIFICATIONS
            for s in _ALL_STATES
        ],
    )
    def test_max_matrix(self, classification: str, state: str):
        expected = _CONVERGE_EXPECTED[classification]
        actual = classify_test_blocking(classification, state, "max")
        assert actual is expected


# ---------------------------------------------------------------------------
# Edge cases and determinism
# ---------------------------------------------------------------------------


class TestExitCodeEdgeCases:
    """Edge cases for compute_exit_code."""

    def test_deterministic_same_input(self):
        """Same input always produces the same result."""
        classifications = {
            "//test:a": _MockClassification("true_fail"),
            "//test:b": _MockClassification("true_pass"),
        }
        r1 = compute_exit_code(classifications, None, "regression")
        r2 = compute_exit_code(classifications, None, "regression")
        assert r1.exit_code == r2.exit_code
        assert r1.blocking_tests == r2.blocking_tests

    def test_disabled_test_non_blocking_regression(self):
        """Disabled test is non-blocking in regression mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "disabled")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("true_fail"),
            }
            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 0

    def test_single_blocking_among_many_non_blocking(self):
        """One blocking test among many non-blocking -> exit 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status.json")
            sf.set_test_state("//test:a", "stable")
            for i in range(10):
                sf.set_test_state(f"//test:ok_{i}", "flaky")
            sf.save()

            classifications = {
                "//test:a": _MockClassification("true_fail"),
            }
            for i in range(10):
                classifications[f"//test:ok_{i}"] = _MockClassification("true_fail")

            summary = compute_exit_code(classifications, sf, "regression")
            assert summary.exit_code == 1
            assert summary.blocking_tests == ["//test:a"]
            assert len(summary.non_blocking_tests) == 10

    def test_summary_type(self):
        """compute_exit_code returns ExitCodeSummary."""
        summary = compute_exit_code({}, None, "regression")
        assert isinstance(summary, ExitCodeSummary)
