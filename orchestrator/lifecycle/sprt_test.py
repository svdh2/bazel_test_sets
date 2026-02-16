"""Unit tests for the SPRT module."""

from __future__ import annotations

import pytest

from orchestrator.lifecycle.sprt import demotion_evaluate, sprt_evaluate


class TestSPRTAccept:
    """Tests for SPRT accept decisions (test is reliable)."""

    def test_accept_perfect_record(self):
        """50/50 passes at 99% reliability -> accept."""
        result = sprt_evaluate(50, 50, 0.99, 0.95)
        assert result == "accept"

    def test_accept_high_pass_rate(self):
        """100/100 passes at 99% reliability -> accept."""
        result = sprt_evaluate(100, 100, 0.99, 0.95)
        assert result == "accept"

    def test_accept_moderate_reliability(self):
        """30/30 at 95% reliability -> accept."""
        result = sprt_evaluate(30, 30, 0.95, 0.95)
        assert result == "accept"

    def test_accept_with_few_failures(self):
        """99/100 at 95% reliability -> accept."""
        result = sprt_evaluate(100, 99, 0.95, 0.95)
        assert result == "accept"


class TestSPRTReject:
    """Tests for SPRT reject decisions (test is unreliable)."""

    def test_reject_low_pass_rate(self):
        """15/20 passes at 99% reliability -> reject."""
        result = sprt_evaluate(20, 15, 0.99, 0.95)
        assert result == "reject"

    def test_reject_very_low_pass_rate(self):
        """10/20 passes at 99% reliability -> reject."""
        result = sprt_evaluate(20, 10, 0.99, 0.95)
        assert result == "reject"

    def test_reject_all_failures(self):
        """0/10 passes -> reject."""
        result = sprt_evaluate(10, 0, 0.99, 0.95)
        assert result == "reject"

    def test_reject_many_failures(self):
        """5/20 passes at 95% reliability -> reject."""
        result = sprt_evaluate(20, 5, 0.95, 0.95)
        assert result == "reject"


class TestSPRTContinue:
    """Tests for SPRT continue decisions (not enough evidence)."""

    def test_continue_zero_runs(self):
        """Zero runs -> continue."""
        result = sprt_evaluate(0, 0, 0.99, 0.95)
        assert result == "continue"

    def test_continue_single_pass(self):
        """1/1 pass at 99% reliability -> continue (not enough data)."""
        result = sprt_evaluate(1, 1, 0.99, 0.95)
        assert result == "continue"

    def test_continue_few_runs(self):
        """5/5 passes at 99% reliability -> continue."""
        result = sprt_evaluate(5, 5, 0.99, 0.95)
        assert result == "continue"


class TestSPRTEdgeCases:
    """Tests for edge cases in SPRT."""

    def test_zero_passes_many_runs(self):
        """0/50 passes -> reject."""
        result = sprt_evaluate(50, 0, 0.99, 0.95)
        assert result == "reject"

    def test_negative_runs_treated_as_continue(self):
        """Negative runs -> continue (guard)."""
        result = sprt_evaluate(-1, 0, 0.99, 0.95)
        assert result == "continue"

    def test_high_significance(self):
        """Higher significance requires more evidence."""
        # At 99.9% significance, 20/20 may still be "continue"
        # (boundary is stricter)
        result = sprt_evaluate(20, 20, 0.99, 0.999)
        assert result in ("accept", "continue")

    def test_low_reliability_threshold(self):
        """Lower reliability threshold accepts more easily."""
        # 90% reliability threshold, 30/30 all-pass
        result = sprt_evaluate(30, 30, 0.90, 0.95)
        assert result == "accept"

    def test_custom_margin(self):
        """Custom margin changes decision boundary."""
        # With a larger margin, accept comes faster
        result_narrow = sprt_evaluate(10, 10, 0.99, 0.95, margin=0.01)
        result_wide = sprt_evaluate(10, 10, 0.99, 0.95, margin=0.10)
        # Wide margin should be at least as decisive
        assert result_wide in ("accept", "continue")

    def test_convergence_to_accept(self):
        """SPRT converges to accept for a reliable test."""
        # Run enough passes that SPRT must decide
        for n in range(1, 200):
            result = sprt_evaluate(n, n, 0.99, 0.95)
            if result == "accept":
                break
        assert result == "accept", "SPRT should accept after enough passes"

    def test_convergence_to_reject(self):
        """SPRT converges to reject for an unreliable test."""
        for n in range(1, 200):
            passes = int(n * 0.80)  # 80% pass rate
            result = sprt_evaluate(n, passes, 0.99, 0.95)
            if result == "reject":
                break
        assert result == "reject", "SPRT should reject low reliability"


class TestDemotionEvaluate:
    """Tests for reverse-chronological SPRT demotion."""

    def test_retain_all_passes(self):
        """All passes in history -> retain."""
        history = [True] * 50
        result = demotion_evaluate(history, 0.99, 0.95)
        assert result == "retain"

    def test_demote_many_failures(self):
        """Many recent failures -> demote."""
        # Recent failures (newest first)
        history = [False] * 10 + [True] * 40
        result = demotion_evaluate(history, 0.99, 0.95)
        assert result == "demote"

    def test_demote_all_failures(self):
        """All failures -> demote."""
        history = [False] * 20
        result = demotion_evaluate(history, 0.99, 0.95)
        assert result == "demote"

    def test_inconclusive_empty_history(self):
        """Empty history -> inconclusive."""
        result = demotion_evaluate([], 0.99, 0.95)
        assert result == "inconclusive"

    def test_inconclusive_single_result(self):
        """Single result may be inconclusive."""
        result = demotion_evaluate([True], 0.99, 0.95)
        assert result in ("inconclusive", "retain")

    def test_retain_recent_passes_old_failures(self):
        """Recent passes outweigh old failures (newest first)."""
        # 50 recent passes, then some old failures
        history = [True] * 50 + [False] * 5
        result = demotion_evaluate(history, 0.99, 0.95)
        assert result == "retain"

    def test_demote_recent_failures_old_passes(self):
        """Recent failures override old passes."""
        # 5 recent failures, then many old passes
        history = [False] * 10 + [True] * 90
        result = demotion_evaluate(history, 0.99, 0.95)
        assert result == "demote"
