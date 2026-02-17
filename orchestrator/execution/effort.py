"""SPRT-based effort rerun engine for converge and max modes.

Provides EffortRunner that reruns tests using the Sequential Probability
Ratio Test to classify each test as true_pass, true_fail, or flake within
a single session.  Only session-local run data is used for SPRT evaluation
(no historic data from previous code states).  All reruns are recorded to
the status file for persistence.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from orchestrator.execution.dag import TestDAG
from orchestrator.execution.executor import TestResult
from orchestrator.lifecycle.sprt import sprt_evaluate
from orchestrator.lifecycle.status import StatusFile


@dataclass
class EffortClassification:
    """SPRT classification result for a single test."""

    test_name: str
    classification: str  # "true_pass", "true_fail", "flake", "undecided"
    initial_status: str  # "passed" or "failed"
    runs: int  # total session runs (including initial)
    passes: int  # total session passes
    sprt_decision: str  # "accept", "reject", "continue", "not_evaluated"


@dataclass
class EffortResult:
    """Aggregate result of an effort rerun session."""

    classifications: dict[str, EffortClassification]
    total_reruns: int


def _classify(
    name: str,
    initial_status: str,
    sprt_decision: str,
    runs: int,
    passes: int,
) -> EffortClassification:
    """Map initial status + SPRT outcome to classification.

    Args:
        name: Test name.
        initial_status: "passed" or "failed" from initial run.
        sprt_decision: SPRT result ("accept", "reject", "continue").
        runs: Total session runs.
        passes: Total session passes.

    Returns:
        EffortClassification with the derived classification.
    """
    if initial_status == "failed":
        if sprt_decision == "accept":
            classification = "flake"
        elif sprt_decision == "reject":
            classification = "true_fail"
        else:
            classification = "undecided"
    else:  # initially passed
        if sprt_decision == "accept":
            classification = "true_pass"
        elif sprt_decision == "reject":
            classification = "flake"
        else:
            classification = "undecided"

    return EffortClassification(
        test_name=name,
        classification=classification,
        initial_status=initial_status,
        runs=runs,
        passes=passes,
        sprt_decision=sprt_decision,
    )


class EffortRunner:
    """Reruns tests using SPRT until classified or budget exhausted.

    Follows the BurnInSweep / HiFiEvaluator pattern: iteratively execute
    tests, record results, evaluate SPRT, and stop when all target tests
    are classified or the per-test budget is exhausted.

    Only session-local run data is used for SPRT evaluation (no historic
    data from previous code states).  All reruns are persisted to the
    status file.
    """

    def __init__(
        self,
        dag: TestDAG,
        status_file: StatusFile,
        commit_sha: str,
        max_reruns: int = 100,
        effort_mode: str = "converge",
        initial_results: list[TestResult] | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.dag = dag
        self.status_file = status_file
        self.commit_sha = commit_sha
        self.max_reruns = max_reruns
        self.effort_mode = effort_mode
        self.initial_results = initial_results or []
        self.timeout = timeout

    def run(self) -> EffortResult:
        """Execute the SPRT rerun loop.

        The initial run (phase 1) is already done by the caller.  This
        method handles phase 2: rerunning tests until SPRT classifies them.

        Returns:
            EffortResult with per-test classifications and total reruns.
        """
        min_reliability = self.status_file.min_reliability
        significance = self.status_file.statistical_significance

        # Build session state from initial results
        session_runs: dict[str, int] = {}
        session_passes: dict[str, int] = {}
        initial_status: dict[str, str] = {}

        for r in self.initial_results:
            if r.status == "dependencies_failed":
                continue
            passed = r.status == "passed"
            session_runs[r.name] = 1
            session_passes[r.name] = 1 if passed else 0
            initial_status[r.name] = r.status

        # Determine targets for SPRT reruns
        if self.effort_mode == "converge":
            targets = {
                name for name, status in initial_status.items()
                if status == "failed"
            }
        else:  # "max"
            targets = set(initial_status.keys())

        # Check if any targets can be immediately classified
        decided: dict[str, EffortClassification] = {}
        for name in list(targets):
            decision = sprt_evaluate(
                session_runs[name],
                session_passes[name],
                min_reliability,
                significance,
            )
            if decision != "continue":
                decided[name] = _classify(
                    name, initial_status[name], decision,
                    session_runs[name], session_passes[name],
                )
                targets.discard(name)

        # Rerun loop
        total_reruns = 0
        per_test_reruns: dict[str, int] = {name: 0 for name in targets}

        while targets:
            for name in list(targets):
                if per_test_reruns[name] >= self.max_reruns:
                    decided[name] = _classify(
                        name, initial_status[name], "continue",
                        session_runs[name], session_passes[name],
                    )
                    targets.discard(name)
                    continue

                result = self._execute_test(name)
                total_reruns += 1
                per_test_reruns[name] += 1

                passed = result.status == "passed"
                session_runs[name] += 1
                session_passes[name] += 1 if passed else 0

                self.status_file.record_run(
                    name, passed, commit=self.commit_sha
                )

                decision = sprt_evaluate(
                    session_runs[name],
                    session_passes[name],
                    min_reliability,
                    significance,
                )
                if decision != "continue":
                    decided[name] = _classify(
                        name, initial_status[name], decision,
                        session_runs[name], session_passes[name],
                    )
                    targets.discard(name)

            self.status_file.save()

        # Classify non-targeted tests (converge: initially-passing tests)
        for name in initial_status:
            if name not in decided:
                status = initial_status[name]
                decided[name] = EffortClassification(
                    test_name=name,
                    classification="true_pass" if status == "passed" else "true_fail",
                    initial_status=status,
                    runs=session_runs[name],
                    passes=session_passes[name],
                    sprt_decision="not_evaluated",
                )

        return EffortResult(
            classifications=decided,
            total_reruns=total_reruns,
        )

    def _execute_test(self, name: str) -> TestResult:
        """Execute a single test via subprocess.

        Follows the same pattern as HiFiEvaluator._execute_test.
        """
        node = self.dag.nodes[name]
        executable = node.executable

        start_time = time.monotonic()
        try:
            proc = subprocess.run(
                [executable],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            duration = time.monotonic() - start_time
            status = "passed" if proc.returncode == 0 else "failed"
            return TestResult(
                name=name,
                assertion=node.assertion,
                status=status,
                duration=duration,
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=str(e),
                exit_code=-1,
            )
