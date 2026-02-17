"""E-value computation for test set verdicts with Type II error control.

Provides per-test E-value computation from history data and test set
combination with controlled aggregate Type II error via safe testing.

E-values measure evidence against the null hypothesis H0 (test is reliable).
Under H0: E[E_i] <= 1 (the E-value is a supermartingale).
Under H1 (test is unreliable): E_i grows exponentially.

Two modes handle non-stationarity across commits:
- Quick: pools evidence across all commits (product of per-commit E-values).
- High-fidelity: uses only current-commit data; reruns until confident.

Test set combination:
- RED:   E_set = (1/N) * sum(E_i) > 1/alpha_set   (at least one test unreliable)
- GREEN: min(S_i) > N/beta_set for all tests        (all tests reliable, union bound)
- UNDECIDED: neither threshold crossed
"""

from __future__ import annotations

import math
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from orchestrator.execution.dag import TestDAG
from orchestrator.execution.executor import TestResult
from orchestrator.lifecycle.status import StatusFile


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TestEValue:
    """E-value computation result for a single test."""

    test_name: str
    e_value: float  # Evidence against reliability (E_i)
    s_value: float  # Evidence for reliability (S_i = 1/E_i)
    log_e_value: float  # log(E_i) for numerical stability
    runs: int  # Total runs included
    passes: int  # Total passes included
    commits_included: int  # Number of distinct commits


@dataclass
class TestSetVerdict:
    """Verdict for an entire test set."""

    verdict: str  # "GREEN", "RED", or "UNDECIDED"
    e_set: float  # Combined E-value: (1/N) * sum(E_i)
    min_s_value: float  # min(S_i) across non-disabled tests
    red_threshold: float  # 1 / alpha_set
    green_threshold: float  # N / beta_set
    n_tests: int  # Number of tests evaluated
    per_test: list[TestEValue] = field(default_factory=list)
    weakest_test: str | None = None  # Name of test with lowest S_i


@dataclass
class HiFiResult:
    """Result of a high-fidelity evaluation phase."""

    verdict: TestSetVerdict
    total_reruns: int  # Total test executions performed
    decided: bool  # True if verdict is GREEN or RED (not UNDECIDED)


# ---------------------------------------------------------------------------
# Per-test E-value computation
# ---------------------------------------------------------------------------


def compute_log_ratio(
    runs: int,
    passes: int,
    min_reliability: float,
    margin: float = 0.10,
) -> float:
    """Compute log(S_i) = log(L(H0)/L(H1)) for a batch of runs.

    Positive values favor H0 (reliable). Negative values favor H1
    (unreliable). This is the same math as sprt.py line 64, factored
    out for reuse.

    Args:
        runs: Number of test runs.
        passes: Number of passing runs.
        min_reliability: H0 reliability threshold (e.g. 0.99).
        margin: Gap between H0 and H1 (default 0.10).

    Returns:
        Log-likelihood ratio.  E_i = exp(-result), S_i = exp(result).
    """
    if runs <= 0:
        return 0.0

    p0 = min(max(min_reliability, 1e-10), 1.0 - 1e-10)
    p1 = max(min_reliability - margin, 1e-10)
    p1 = min(p1, 1.0 - 1e-10)

    failures = runs - passes

    return passes * math.log(p0 / p1) + failures * math.log(
        (1.0 - p0) / (1.0 - p1)
    )


def compute_test_e_value_quick(
    test_name: str,
    history: list[dict[str, Any]],
    min_reliability: float,
    margin: float = 0.10,
) -> TestEValue:
    """Compute E-value for a test in quick mode (all history pooled).

    Groups history entries by commit SHA.  For each commit, computes
    the per-commit log-likelihood ratio.  The total log S-value is the
    sum of per-commit log ratios (equivalent to the product of
    per-commit S-values).  Entries with ``commit=None`` are treated as
    independent single-run groups.

    Args:
        test_name: Test label.
        history: Newest-first list of {"passed": bool, "commit": str | None}.
        min_reliability: H0 reliability threshold.
        margin: Separation between H0 and H1.

    Returns:
        TestEValue with combined E-value across all commits.
    """
    if not history:
        return TestEValue(
            test_name=test_name,
            e_value=1.0,
            s_value=1.0,
            log_e_value=0.0,
            runs=0,
            passes=0,
            commits_included=0,
        )

    # Group by commit
    commit_groups: dict[str, list[bool]] = defaultdict(list)
    none_counter = 0
    for entry in history:
        commit = entry.get("commit")
        if commit is None:
            # Each None-commit entry is its own group
            commit_groups[f"__none_{none_counter}__"] = [entry["passed"]]
            none_counter += 1
        else:
            commit_groups[commit].append(entry["passed"])

    total_log_s = 0.0
    total_runs = 0
    total_passes = 0

    for outcomes in commit_groups.values():
        runs = len(outcomes)
        passes = sum(outcomes)
        total_runs += runs
        total_passes += passes
        total_log_s += compute_log_ratio(runs, passes, min_reliability, margin)

    log_e = -total_log_s
    # Clamp to avoid overflow
    log_e = max(min(log_e, 700.0), -700.0)

    e_value = math.exp(log_e)
    s_value = math.exp(-log_e)

    return TestEValue(
        test_name=test_name,
        e_value=e_value,
        s_value=s_value,
        log_e_value=log_e,
        runs=total_runs,
        passes=total_passes,
        commits_included=len(commit_groups),
    )


def compute_test_e_value_hifi(
    test_name: str,
    history: list[dict[str, Any]],
    current_commit: str,
    min_reliability: float,
    margin: float = 0.10,
) -> TestEValue:
    """Compute E-value for a test in high-fidelity mode (current commit only).

    Filters history to entries matching ``current_commit`` and computes
    the log-ratio from those runs only.  No cross-commit evidence pooling.

    Args:
        test_name: Test label.
        history: Newest-first list of {"passed": bool, "commit": str | None}.
        current_commit: The commit SHA to filter on.
        min_reliability: H0 reliability threshold.
        margin: Separation between H0 and H1.

    Returns:
        TestEValue with E-value from current commit only.
    """
    matching = [e for e in history if e.get("commit") == current_commit]

    if not matching:
        return TestEValue(
            test_name=test_name,
            e_value=1.0,
            s_value=1.0,
            log_e_value=0.0,
            runs=0,
            passes=0,
            commits_included=0,
        )

    runs = len(matching)
    passes = sum(e["passed"] for e in matching)
    log_s = compute_log_ratio(runs, passes, min_reliability, margin)
    log_e = -log_s
    log_e = max(min(log_e, 700.0), -700.0)

    return TestEValue(
        test_name=test_name,
        e_value=math.exp(log_e),
        s_value=math.exp(-log_e),
        log_e_value=log_e,
        runs=runs,
        passes=passes,
        commits_included=1,
    )


# ---------------------------------------------------------------------------
# Test set verdict
# ---------------------------------------------------------------------------


def compute_test_set_verdict(
    test_e_values: list[TestEValue],
    alpha_set: float = 0.05,
    beta_set: float = 0.05,
) -> TestSetVerdict:
    """Compute test set verdict from per-test E-values.

    RED evidence:  ``E_set = (1/N) * sum(E_i) > 1/alpha_set``
      At least one test is unreliable (Markov on average E-value;
      controls family-wise error rate at level ``alpha_set``).

    GREEN evidence: ``min(S_i) > N / beta_set`` for all tests
      All tests are reliable (union bound over N tests; if any
      ``S_i < N/beta_set``, aggregate Type II error exceeds ``beta_set``).

    UNDECIDED: neither threshold crossed.

    Args:
        test_e_values: Per-test E-value results (non-disabled tests only).
        alpha_set: Type I error rate for RED verdict (false alarm).
        beta_set: Type II error rate for GREEN verdict (miss rate).

    Returns:
        TestSetVerdict with verdict and supporting data.
    """
    n = len(test_e_values)

    if n == 0:
        return TestSetVerdict(
            verdict="GREEN",
            e_set=0.0,
            min_s_value=float("inf"),
            red_threshold=1.0 / alpha_set,
            green_threshold=0.0,
            n_tests=0,
            per_test=[],
            weakest_test=None,
        )

    e_set = sum(tv.e_value for tv in test_e_values) / n
    red_threshold = 1.0 / alpha_set

    min_s = min(tv.s_value for tv in test_e_values)
    green_threshold = n / beta_set

    weakest = min(test_e_values, key=lambda tv: tv.s_value)

    if e_set > red_threshold:
        verdict = "RED"
    elif min_s > green_threshold:
        verdict = "GREEN"
    else:
        verdict = "UNDECIDED"

    return TestSetVerdict(
        verdict=verdict,
        e_set=e_set,
        min_s_value=min_s,
        red_threshold=red_threshold,
        green_threshold=green_threshold,
        n_tests=n,
        per_test=list(test_e_values),
        weakest_test=weakest.test_name,
    )


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def evaluate_test_set(
    test_names: list[str],
    status_file: StatusFile,
    mode: str = "quick",
    current_commit: str | None = None,
    alpha_set: float = 0.05,
    beta_set: float = 0.05,
    margin: float = 0.10,
) -> TestSetVerdict:
    """Evaluate a test set verdict from status file history.

    Reads per-test history from *status_file* and computes the
    aggregate E-value verdict.

    Args:
        test_names: Non-disabled test names in the set.
        status_file: StatusFile instance with history data.
        mode: ``"quick"`` (pool across commits) or ``"hifi"``
            (current commit only).
        current_commit: Required when *mode* is ``"hifi"``.
        alpha_set: Type I error for RED.
        beta_set: Type II error for GREEN.
        margin: SPRT margin parameter.

    Returns:
        TestSetVerdict.

    Raises:
        ValueError: If *mode* is ``"hifi"`` and *current_commit* is None.
    """
    if mode == "hifi" and current_commit is None:
        raise ValueError("current_commit is required for hifi mode")

    min_reliability = status_file.min_reliability

    test_e_values: list[TestEValue] = []
    for name in test_names:
        history = status_file.get_test_history(name)
        if mode == "quick":
            tv = compute_test_e_value_quick(
                name, history, min_reliability, margin
            )
        else:
            assert current_commit is not None
            tv = compute_test_e_value_hifi(
                name, history, current_commit, min_reliability, margin
            )
        test_e_values.append(tv)

    return compute_test_set_verdict(test_e_values, alpha_set, beta_set)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def verdict_to_dict(verdict: TestSetVerdict) -> dict[str, Any]:
    """Serialize a TestSetVerdict to a JSON-compatible dict."""
    return {
        "verdict": verdict.verdict,
        "e_set": round(verdict.e_set, 6),
        "min_s_value": round(verdict.min_s_value, 6),
        "red_threshold": round(verdict.red_threshold, 6),
        "green_threshold": round(verdict.green_threshold, 6),
        "n_tests": verdict.n_tests,
        "weakest_test": verdict.weakest_test,
        "per_test": [
            {
                "test_name": tv.test_name,
                "e_value": round(tv.e_value, 6),
                "s_value": round(tv.s_value, 6),
                "runs": tv.runs,
                "passes": tv.passes,
                "commits_included": tv.commits_included,
            }
            for tv in verdict.per_test
        ],
    }


# ---------------------------------------------------------------------------
# High-fidelity evaluator
# ---------------------------------------------------------------------------


class HiFiEvaluator:
    """Reruns tests at the current commit until E-value thresholds are met.

    Follows the BurnInSweep pattern: iteratively execute tests, record
    results in the StatusFile, re-evaluate the verdict, and stop when
    the test set verdict is decided or the budget is exhausted.
    """

    def __init__(
        self,
        dag: TestDAG,
        status_file: StatusFile,
        commit_sha: str,
        alpha_set: float = 0.05,
        beta_set: float = 0.05,
        margin: float = 0.10,
        max_reruns: int = 100,
        timeout: float = 300.0,
    ) -> None:
        self.dag = dag
        self.status_file = status_file
        self.commit_sha = commit_sha
        self.alpha_set = alpha_set
        self.beta_set = beta_set
        self.margin = margin
        self.max_reruns = max_reruns
        self.timeout = timeout

    def evaluate(self, test_names: list[str]) -> HiFiResult:
        """Run the high-fidelity evaluation loop.

        For each iteration:
        1. Evaluate the current verdict from status file history.
        2. If decided (GREEN or RED), return immediately.
        3. Otherwise, rerun all tests once and record results.
        4. Repeat until decided or budget exhausted.

        Args:
            test_names: Non-disabled test names to evaluate.

        Returns:
            HiFiResult with final verdict and total rerun count.
        """
        total_reruns = 0

        for _ in range(self.max_reruns):
            verdict = evaluate_test_set(
                test_names,
                self.status_file,
                mode="hifi",
                current_commit=self.commit_sha,
                alpha_set=self.alpha_set,
                beta_set=self.beta_set,
                margin=self.margin,
            )

            if verdict.verdict in ("GREEN", "RED"):
                return HiFiResult(
                    verdict=verdict,
                    total_reruns=total_reruns,
                    decided=True,
                )

            # Rerun all tests once
            for test_name in test_names:
                if test_name not in self.dag.nodes:
                    continue
                result = self._execute_test(test_name)
                total_reruns += 1
                passed = result.status == "passed"
                self.status_file.record_run(
                    test_name, passed, commit=self.commit_sha
                )
            self.status_file.save()

        # Budget exhausted — return final verdict
        final_verdict = evaluate_test_set(
            test_names,
            self.status_file,
            mode="hifi",
            current_commit=self.commit_sha,
            alpha_set=self.alpha_set,
            beta_set=self.beta_set,
            margin=self.margin,
        )
        return HiFiResult(
            verdict=final_verdict,
            total_reruns=total_reruns,
            decided=final_verdict.verdict in ("GREEN", "RED"),
        )

    def _execute_test(self, name: str) -> TestResult:
        """Execute a single test via subprocess.

        Args:
            name: Test node name.

        Returns:
            TestResult with execution outcome.
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
