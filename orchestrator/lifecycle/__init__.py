"""Burn-in lifecycle: SPRT decisions, sweep loops, state tracking, and E-value verdicts."""

from orchestrator.lifecycle.burnin import BurnInSweep, SweepResult, filter_tests_by_state, handle_stable_failure
from orchestrator.lifecycle.e_values import (
    HiFiEvaluator,
    HiFiResult,
    TestEValue,
    TestSetVerdict,
    compute_test_set_verdict,
    evaluate_test_set,
    verdict_to_dict,
)
from orchestrator.lifecycle.sprt import demotion_evaluate, sprt_evaluate
from orchestrator.lifecycle.status import StatusFile

__all__ = [
    "BurnInSweep",
    "HiFiEvaluator",
    "HiFiResult",
    "StatusFile",
    "SweepResult",
    "TestEValue",
    "TestSetVerdict",
    "compute_test_set_verdict",
    "demotion_evaluate",
    "evaluate_test_set",
    "filter_tests_by_state",
    "handle_stable_failure",
    "sprt_evaluate",
    "verdict_to_dict",
]
