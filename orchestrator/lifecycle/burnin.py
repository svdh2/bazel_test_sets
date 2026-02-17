"""Burn-in sweep loop, stable demotion logic, and result processing.

Implements the burn-in lifecycle:
- Sweep loop: runs burning_in tests until SPRT decides each one
- Stable demotion: re-runs failed stable tests to evaluate demotion
- Result processing: records orchestrator results and drives state transitions

State transitions:
- new -> burning_in (via CI tool)
- burning_in -> stable (SPRT accept)
- burning_in -> flaky (SPRT reject)
- stable -> flaky (demotion after repeated failure)
- stable -> burning_in (suspicious: SPRT inconclusive after failure)
- flaky -> burning_in (via CI tool deflake)
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from orchestrator.execution.dag import TestDAG
from orchestrator.execution.executor import TestResult
from orchestrator.lifecycle.sprt import demotion_evaluate, sprt_evaluate
from orchestrator.lifecycle.status import StatusFile


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
        commit_sha: str | None = None,
        max_iterations: int = 200,
        timeout: float = 300.0,
    ) -> None:
        self.dag = dag
        self.status_file = status_file
        self.commit_sha = commit_sha
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
                self.status_file.record_run(
                    test_name, passed, commit=self.commit_sha
                )
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
    commit_sha: str | None = None,
    max_reruns: int = 20,
    timeout: float = 300.0,
) -> str:
    """Handle a failed stable test by evaluating demotion.

    Re-runs the test and evaluates reverse-chronological SPRT using the
    full persisted history (not just current-session re-runs). This
    enables cross-run demotion detection for intermittent failures.

    Args:
        test_name: Name of the failed stable test.
        dag: Test DAG for execution.
        status_file: StatusFile for state management.
        commit_sha: Git commit SHA the runs belong to, or None.
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

        status_file.record_run(test_name, passed, commit=commit_sha)
        status_file.save()

        # Use the full persisted history for demotion evaluation
        history = status_file.get_test_history(test_name)

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


def sync_disabled_state(
    dag: TestDAG,
    status_file: StatusFile,
) -> list[tuple[str, str, str, str]]:
    """Synchronize disabled flags from the DAG with the status file.

    For each test in the DAG:
    - If disabled in DAG and state != "disabled": transition to "disabled".
    - If NOT disabled in DAG but state == "disabled": transition to "new".

    Args:
        dag: Test DAG with disabled flags from the manifest.
        status_file: StatusFile for state management.

    Returns:
        List of (event_type, test_name, old_state, new_state) tuples.
    """
    events: list[tuple[str, str, str, str]] = []

    for name, node in dag.nodes.items():
        current_state = status_file.get_test_state(name)

        if node.disabled and current_state != "disabled":
            old = current_state or "new"
            status_file.set_test_state(name, "disabled", runs=0, passes=0)
            events.append(("disabled", name, old, "disabled"))
        elif not node.disabled and current_state == "disabled":
            status_file.set_test_state(name, "new", runs=0, passes=0)
            events.append(("re-enabled", name, "disabled", "new"))

    if events:
        status_file.save()

    return events


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


def process_results(
    results: list[TestResult],
    status_file: StatusFile,
    commit_sha: str | None = None,
) -> list[tuple[str, str, str, str]]:
    """Record orchestrator test results and evaluate lifecycle transitions.

    For each result (skipping dependencies_failed — test didn't run):
    - Records the run via status_file.record_run()
    - burning_in: evaluates SPRT on aggregate counters
        - accept → stable, reject → flaky
    - stable + failed: evaluates demotion via SPRT on full history
        - demote → flaky, inconclusive → burning_in, retain → no change
    - flaky / new: just records, no evaluation

    Args:
        results: Test results from the orchestrator executor.
        status_file: StatusFile for state management and persistence.
        commit_sha: Git commit SHA to record with each run, or None.

    Returns:
        List of (event_type, test_name, old_state, new_state) tuples
        for each state transition that occurred.
    """
    events: list[tuple[str, str, str, str]] = []

    for result in results:
        if result.status == "dependencies_failed":
            continue

        # Look up state BEFORE recording (record_run creates "new" entries)
        state = status_file.get_test_state(result.name)

        if state == "disabled":
            continue

        # Record the run
        passed = result.status == "passed"
        status_file.record_run(result.name, passed, commit=commit_sha)
        status_file.save()

        if state == "burning_in":
            entry = status_file.get_test_entry(result.name)
            if entry is None:
                continue
            decision = sprt_evaluate(
                entry["runs"],
                entry["passes"],
                status_file.min_reliability,
                status_file.statistical_significance,
            )
            if decision == "accept":
                status_file.set_test_state(
                    result.name,
                    "stable",
                    runs=entry["runs"],
                    passes=entry["passes"],
                )
                status_file.save()
                events.append(("accepted", result.name, "burning_in", "stable"))
            elif decision == "reject":
                status_file.set_test_state(
                    result.name,
                    "flaky",
                    runs=entry["runs"],
                    passes=entry["passes"],
                )
                status_file.save()
                events.append(("rejected", result.name, "burning_in", "flaky"))

        elif state in ("stable", None) and not passed:
            # Default-stable (None) or explicitly stable test failed.
            # Only evaluate demotion for explicitly stable tests.
            if state != "stable":
                continue
            history = status_file.get_test_history(result.name)
            decision = demotion_evaluate(
                history,
                status_file.min_reliability,
                status_file.statistical_significance,
            )
            if decision == "demote":
                status_file.set_test_state(result.name, "flaky")
                status_file.save()
                events.append(("demoted", result.name, "stable", "flaky"))
            elif decision == "inconclusive":
                # Suspicious — can't confidently retain, move to burn-in
                # for closer monitoring. Preserve counters and history.
                status_file.set_test_state(result.name, "burning_in")
                status_file.save()
                events.append(("suspicious", result.name, "stable", "burning_in"))

    return events
