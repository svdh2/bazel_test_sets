"""Burn-in sweep loop and stable demotion logic.

Implements the burn-in lifecycle:
- Sweep loop: runs burning_in tests until SPRT decides each one
- Stable demotion: re-runs failed stable tests to evaluate demotion

State transitions:
- new -> burning_in (via CI tool)
- burning_in -> stable (SPRT accept)
- burning_in -> flaky (SPRT reject)
- stable -> flaky (demotion after repeated failure)
- flaky -> burning_in (via CI tool deflake)
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from orchestrator.dag import TestDAG
from orchestrator.executor import TestResult
from orchestrator.sprt import demotion_evaluate, sprt_evaluate
from orchestrator.status import StatusFile


@dataclass
class SweepResult:
    """Result of a burn-in sweep."""

    decided: dict[str, str]  # test_name -> final state (stable/flaky)
    undecided: list[str]  # tests still burning_in
    total_runs: int  # total test executions performed


class BurnInSweep:
    """Executes burn-in sweep loop for burning_in tests.

    Runs each burning_in test, evaluates SPRT after each run, and
    transitions tests to stable or flaky. Repeats until all tests
    are decided or max_iterations is reached.
    """

    def __init__(
        self,
        dag: TestDAG,
        status_file: StatusFile,
        max_iterations: int = 200,
        timeout: float = 300.0,
    ) -> None:
        self.dag = dag
        self.status_file = status_file
        self.max_iterations = max_iterations
        self.timeout = timeout

    def run(self, test_names: list[str] | None = None) -> SweepResult:
        """Execute the burn-in sweep loop.

        Args:
            test_names: Specific tests to burn in. If None, runs all
                burning_in tests from the status file.

        Returns:
            SweepResult with decided and undecided tests.
        """
        # Get burning_in tests
        if test_names is not None:
            burning_in = [
                t
                for t in test_names
                if self.status_file.get_test_state(t) == "burning_in"
            ]
        else:
            burning_in = self.status_file.get_tests_by_state("burning_in")

        decided: dict[str, str] = {}
        total_runs = 0
        iteration = 0

        while burning_in and iteration < self.max_iterations:
            iteration += 1

            for test_name in list(burning_in):
                # Check if test is in the DAG
                if test_name not in self.dag.nodes:
                    continue

                # Run the test
                result = self._execute_test(test_name)
                total_runs += 1

                # Record the run
                passed = result.status == "passed"
                self.status_file.record_run(test_name, passed)
                self.status_file.save()  # Incremental save for crash recovery

                # Evaluate SPRT
                entry = self.status_file.get_test_entry(test_name)
                if entry is None:
                    continue

                decision = sprt_evaluate(
                    entry["runs"],
                    entry["passes"],
                    self.status_file.min_reliability,
                    self.status_file.statistical_significance,
                )

                if decision == "accept":
                    self.status_file.set_test_state(
                        test_name,
                        "stable",
                        runs=entry["runs"],
                        passes=entry["passes"],
                    )
                    self.status_file.save()
                    decided[test_name] = "stable"
                    burning_in.remove(test_name)
                elif decision == "reject":
                    self.status_file.set_test_state(
                        test_name,
                        "flaky",
                        runs=entry["runs"],
                        passes=entry["passes"],
                    )
                    self.status_file.save()
                    decided[test_name] = "flaky"
                    burning_in.remove(test_name)
                # else: continue (keep in burning_in)

        return SweepResult(
            decided=decided,
            undecided=burning_in,
            total_runs=total_runs,
        )

    def _execute_test(self, name: str) -> TestResult:
        """Execute a single test.

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


def handle_stable_failure(
    test_name: str,
    dag: TestDAG,
    status_file: StatusFile,
    max_reruns: int = 20,
    timeout: float = 300.0,
) -> str:
    """Handle a failed stable test by evaluating demotion.

    Re-runs the test and evaluates reverse-chronological SPRT to
    determine if the test should be demoted to flaky.

    Args:
        test_name: Name of the failed stable test.
        dag: Test DAG for execution.
        status_file: StatusFile for state management.
        max_reruns: Maximum re-runs for demotion evaluation.
        timeout: Test execution timeout.

    Returns:
        "demote" if test is demoted to flaky,
        "retain" if test stays stable,
        "inconclusive" if unable to decide.
    """
    if test_name not in dag.nodes:
        return "inconclusive"

    node = dag.nodes[test_name]
    rerun_results: list[bool] = []

    for _ in range(max_reruns):
        # Run the test
        try:
            proc = subprocess.run(
                [node.executable],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            passed = proc.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            passed = False

        rerun_results.append(passed)
        status_file.record_run(test_name, passed)
        status_file.save()

        # Build history for demotion evaluation (newest first)
        # Use rerun results as the recent history
        history = list(reversed(rerun_results))

        decision = demotion_evaluate(
            history,
            status_file.min_reliability,
            status_file.statistical_significance,
        )

        if decision == "demote":
            entry = status_file.get_test_entry(test_name)
            status_file.set_test_state(
                test_name,
                "flaky",
                runs=entry["runs"] if entry else 0,
                passes=entry["passes"] if entry else 0,
            )
            status_file.save()
            return "demote"
        elif decision == "retain":
            return "retain"

    return "inconclusive"


def filter_tests_by_state(
    dag: TestDAG,
    status_file: StatusFile,
    include_states: set[str] | None = None,
) -> list[str]:
    """Filter DAG tests by their burn-in state.

    Args:
        dag: Test DAG.
        status_file: StatusFile for state lookup.
        include_states: Set of states to include. If None, includes
            only "stable" tests (and tests not in the status file,
            which are treated as stable by default).

    Returns:
        List of test names that match the filter.
    """
    if include_states is None:
        include_states = {"stable"}

    result = []
    for name in dag.nodes:
        state = status_file.get_test_state(name)
        if state is None:
            # Tests not in status file are treated as stable
            if "stable" in include_states:
                result.append(name)
        elif state in include_states:
            result.append(name)

    return result
