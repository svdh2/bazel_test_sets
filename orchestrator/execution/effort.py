"""SPRT-based effort rerun engine for converge and max modes.

Provides EffortRunner that reruns tests using the Sequential Probability
Ratio Test to classify each test as true_pass, true_fail, or flake.

When ``target_hashes`` are provided, SPRT evaluation pools evidence from
prior sessions that share the same target hash.  This enables cross-session
evidence accumulation: a test that ran 5 times in a previous session and
10 times now can reach a decision based on all 15 data points, as long as
the target hash hasn't changed.  When ``target_hashes`` is None, only
session-local run data is used (backward compatible).

All reruns are recorded to the status file for persistence.
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

    When *target_hashes* is provided, SPRT evaluation pools evidence from
    prior sessions with matching hashes.  When *target_hashes* is ``None``,
    only session-local run data is used (backward compatible).
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
        target_hashes: dict[str, str] | None = None,
    ) -> None:
        self.dag = dag
        self.status_file = status_file
        self.commit_sha = commit_sha
        self.max_reruns = max_reruns
        self.effort_mode = effort_mode
        self.initial_results = initial_results or []
        self.timeout = timeout
        self.target_hashes = target_hashes

    def _get_target_hash(self, name: str) -> str | None:
        """Return the target hash for *name*, or ``None`` if not available."""
        if self.target_hashes is None:
            return None
        return self.target_hashes.get(name)

    def _load_prior_evidence(
        self, name: str,
    ) -> tuple[int, int]:
        """Load prior same-hash evidence from the status file.

        When ``target_hashes`` is provided and a hash exists for *name*,
        queries the status file for history entries recorded with the same
        hash.  Returns a (runs, passes) tuple representing the prior
        evidence to seed the SPRT counters.

        Returns:
            ``(0, 0)`` when ``target_hashes`` is ``None``, the test has no
            hash, or no prior history exists.
        """
        target_hash = self._get_target_hash(name)
        if target_hash is None:
            return 0, 0

        from orchestrator.lifecycle.status import runs_and_passes_from_history

        same_hash_history = self.status_file.get_same_hash_history(
            name, target_hash,
        )
        return runs_and_passes_from_history(same_hash_history)

    def run(self) -> EffortResult:
        """Execute the SPRT rerun loop.

        The initial run (phase 1) is already done by the caller.  This
        method handles phase 2: rerunning tests until SPRT classifies them.

        When ``target_hashes`` is provided, SPRT evaluation uses the
        combined evidence from prior sessions (same-hash history in the
        status file) plus the current session.  This enables cross-session
        convergence.

        Returns:
            EffortResult with per-test classifications and total reruns.
        """
        min_reliability = self.status_file.min_reliability
        significance = self.status_file.statistical_significance

        # Build session state from initial results.
        # ``total_runs`` / ``total_passes`` include prior same-hash evidence
        # (when target_hashes is set) plus the current session data.
        # ``session_runs`` / ``session_passes`` track only the current
        # session for reporting purposes.
        session_runs: dict[str, int] = {}
        session_passes: dict[str, int] = {}
        total_runs: dict[str, int] = {}
        total_passes: dict[str, int] = {}
        initial_status: dict[str, str] = {}

        for r in self.initial_results:
            if r.status == "dependencies_failed":
                continue
            passed = r.status == "passed"
            session_runs[r.name] = 1
            session_passes[r.name] = 1 if passed else 0
            initial_status[r.name] = r.status

            # Load prior same-hash evidence
            prior_runs, prior_passes = self._load_prior_evidence(r.name)
            total_runs[r.name] = prior_runs + 1
            total_passes[r.name] = prior_passes + (1 if passed else 0)

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
                total_runs[name],
                total_passes[name],
                min_reliability,
                significance,
            )
            if decision != "continue":
                decided[name] = _classify(
                    name, initial_status[name], decision,
                    total_runs[name], total_passes[name],
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
                        total_runs[name], total_passes[name],
                    )
                    targets.discard(name)
                    continue

                result = self._execute_test(name)
                total_reruns += 1
                per_test_reruns[name] += 1

                passed = result.status == "passed"
                session_runs[name] += 1
                session_passes[name] += 1 if passed else 0
                total_runs[name] += 1
                total_passes[name] += 1 if passed else 0

                self.status_file.record_run(
                    name, passed, commit=self.commit_sha,
                    target_hash=self._get_target_hash(name),
                )

                decision = sprt_evaluate(
                    total_runs[name],
                    total_passes[name],
                    min_reliability,
                    significance,
                )
                if decision != "continue":
                    decided[name] = _classify(
                        name, initial_status[name], decision,
                        total_runs[name], total_passes[name],
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
                    runs=total_runs[name],
                    passes=total_passes[name],
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
