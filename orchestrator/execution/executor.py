"""Test executors with diagnostic and detection mode support.

Provides SequentialExecutor for single-threaded execution and AsyncExecutor
for sliding window parallel execution. Both support dependency failure
propagation and max_failures threshold.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from orchestrator.execution.dag import TestDAG


@dataclass
class TestResult:
    """Result of a single test execution."""

    name: str
    assertion: str
    status: str  # passed, failed, dependencies_failed
    duration: float = 0.0
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


class SequentialExecutor:
    """Executes tests sequentially in DAG order.

    Supports diagnostic mode (leaves-first with dependency gating) and
    detection mode (roots-first without dependency gating).
    """

    def __init__(
        self,
        dag: TestDAG,
        mode: str = "diagnostic",
        max_failures: int | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.dag = dag
        self.mode = mode
        self.max_failures = max_failures
        self.timeout = timeout
        self.results: dict[str, TestResult] = {}
        self._failure_count = 0

    def execute(self) -> list[TestResult]:
        """Execute all tests in the DAG.

        Returns:
            List of TestResult objects in execution order.
        """
        # Get execution order based on mode
        if self.mode == "diagnostic":
            order = self.dag.topological_sort_leaves_first()
        elif self.mode == "detection":
            order = self.dag.bfs_roots_first()
        else:
            raise ValueError(f"Unknown execution mode: {self.mode}")

        result_list: list[TestResult] = []

        for name in order:
            # Check max_failures threshold
            if self.max_failures is not None and self._failure_count >= self.max_failures:
                break

            node = self.dag.nodes[name]

            # In diagnostic mode, check if dependencies have failed
            if self.mode == "diagnostic":
                dep_failed = self._check_dependency_failures(name)
                if dep_failed:
                    result = TestResult(
                        name=name,
                        assertion=node.assertion,
                        status="dependencies_failed",
                    )
                    self.results[name] = result
                    result_list.append(result)
                    continue

            # Execute the test
            result = self._run_test(name)
            self.results[name] = result
            result_list.append(result)

            if result.status == "failed":
                self._failure_count += 1

        return result_list

    def _check_dependency_failures(self, name: str) -> bool:
        """Check if any dependency of the given test has failed.

        Args:
            name: Test node name.

        Returns:
            True if any dependency has a failure status.
        """
        deps = self.dag.get_dependencies(name)
        for dep in deps:
            if dep in self.results:
                status = self.results[dep].status
                if status in ("failed", "dependencies_failed"):
                    return True
        return False

    def _run_test(self, name: str) -> TestResult:
        """Run a single test by executing its executable.

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
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=f"Test timed out after {self.timeout} seconds",
                exit_code=-1,
            )
        except FileNotFoundError:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=f"Executable not found: {executable}",
                exit_code=-1,
            )
        except OSError as e:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=f"OS error running test: {e}",
                exit_code=-1,
            )


class AsyncExecutor:
    """Executes tests in parallel using asyncio with a sliding window.

    Uses a semaphore to limit concurrency to max_parallel tests.
    Tests are dispatched as soon as their dependencies are satisfied,
    providing streaming execution without batching delays.

    Supports combined statuses for the race condition case where a
    dependency fails while a dependent test is already running:
    - passed+dependencies_failed: test passed but a dep failed during execution
    - failed+dependencies_failed: test failed and a dep also failed during execution
    """

    def __init__(
        self,
        dag: TestDAG,
        mode: str = "diagnostic",
        max_failures: int | None = None,
        max_parallel: int | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.dag = dag
        self.mode = mode
        self.max_failures = max_failures
        self.max_parallel = max_parallel or os.cpu_count() or 4
        self.timeout = timeout
        self.results: dict[str, TestResult] = {}
        self._failure_count = 0
        self._result_list: list[TestResult] = []
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        # Track when each test started (for combined status determination)
        self._start_times: dict[str, float] = {}
        # Track when dependency failures were recorded
        self._dep_failure_times: dict[str, float] = {}

    def execute(self) -> list[TestResult]:
        """Execute all tests in the DAG.

        Returns:
            List of TestResult objects in completion order.
        """
        return asyncio.run(self._execute_async())

    async def _execute_async(self) -> list[TestResult]:
        """Async implementation of test execution."""
        # Validate mode
        if self.mode not in ("diagnostic", "detection"):
            raise ValueError(f"Unknown execution mode: {self.mode}")

        if not self.dag.nodes:
            return []

        # Verify DAG is acyclic (both sort methods do this)
        if self.mode == "diagnostic":
            self.dag.topological_sort_leaves_first()
        else:
            self.dag.bfs_roots_first()

        semaphore = asyncio.Semaphore(self.max_parallel)

        # Track which tests are pending, running, or done
        pending: set[str] = set(self.dag.nodes.keys())
        running: set[str] = set()
        done: set[str] = set()

        # Event to wake up the scheduler when a test finishes
        wake = asyncio.Event()

        async def run_test(name: str) -> None:
            """Run a single test and update state."""
            async with semaphore:
                if self._stop_event.is_set():
                    async with self._lock:
                        running.discard(name)
                        done.add(name)
                    wake.set()
                    return

                self._start_times[name] = time.monotonic()
                result = await self._run_test_async(name)

                async with self._lock:
                    # Check for combined status in diagnostic mode
                    if self.mode == "diagnostic":
                        result = self._apply_combined_status(name, result)

                    self.results[name] = result
                    self._result_list.append(result)

                    # Record dep failure time for downstream dependents
                    if result.status in (
                        "failed",
                        "dependencies_failed",
                        "failed+dependencies_failed",
                    ):
                        self._dep_failure_times[name] = time.monotonic()

                    if result.status in ("failed", "failed+dependencies_failed"):
                        self._failure_count += 1
                        if (
                            self.max_failures is not None
                            and self._failure_count >= self.max_failures
                        ):
                            self._stop_event.set()

                    running.discard(name)
                    done.add(name)

                wake.set()

        tasks: set[asyncio.Task[None]] = set()

        while pending or running:
            if self._stop_event.is_set() and not running:
                break

            # Find tests ready to dispatch
            ready: list[str] = []
            for name in list(pending):
                if self._stop_event.is_set():
                    break

                deps = set(self.dag.get_dependencies(name))

                if self.mode == "diagnostic":
                    # Check if any dep has failed
                    dep_failed = any(
                        dep in self.results
                        and self.results[dep].status
                        in (
                            "failed",
                            "dependencies_failed",
                            "passed+dependencies_failed",
                            "failed+dependencies_failed",
                        )
                        for dep in deps
                    )
                    if dep_failed:
                        # Mark as dependencies_failed immediately
                        node = self.dag.nodes[name]
                        result = TestResult(
                            name=name,
                            assertion=node.assertion,
                            status="dependencies_failed",
                        )
                        self.results[name] = result
                        self._result_list.append(result)
                        self._dep_failure_times[name] = time.monotonic()
                        pending.discard(name)
                        done.add(name)
                        continue

                    # All deps must be done (not just not-failed)
                    if deps.issubset(done):
                        ready.append(name)

                elif self.mode == "detection":
                    # In detection mode, run whenever deps are done
                    # (regardless of their status)
                    if deps.issubset(done):
                        ready.append(name)

            for name in ready:
                pending.discard(name)
                running.add(name)
                task = asyncio.create_task(run_test(name))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

            if not ready and (pending or running):
                # Wait for a test to finish
                wake.clear()
                await wake.wait()

        # Wait for any remaining tasks
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return self._result_list

    def _apply_combined_status(
        self, name: str, result: TestResult
    ) -> TestResult:
        """Check if a dependency failed while this test was running.

        If so, apply the combined status (e.g., passed+dependencies_failed).

        Args:
            name: Test name.
            result: The test's execution result.

        Returns:
            TestResult with potentially updated combined status.
        """
        if result.status not in ("passed", "failed"):
            return result

        test_start = self._start_times.get(name, 0)
        deps = self.dag.get_dependencies(name)

        dep_failed_during = False
        for dep in deps:
            dep_fail_time = self._dep_failure_times.get(dep)
            if dep_fail_time is not None and dep_fail_time > test_start:
                dep_failed_during = True
                break

        if dep_failed_during:
            combined_status = f"{result.status}+dependencies_failed"
            return TestResult(
                name=result.name,
                assertion=result.assertion,
                status=combined_status,
                duration=result.duration,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
            )

        return result

    async def _run_test_async(self, name: str) -> TestResult:
        """Run a single test asynchronously using a thread pool.

        Uses subprocess.run in a thread executor to avoid asyncio subprocess
        child watcher issues in containerized environments.

        Args:
            name: Test node name.

        Returns:
            TestResult with execution outcome.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run_test_sync, name)

    def _run_test_sync(self, name: str) -> TestResult:
        """Run a single test synchronously (called from thread pool).

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
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=f"Test timed out after {self.timeout} seconds",
                exit_code=-1,
            )
        except FileNotFoundError:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=f"Executable not found: {executable}",
                exit_code=-1,
            )
        except OSError as e:
            duration = time.monotonic() - start_time
            return TestResult(
                name=name,
                assertion=node.assertion,
                status="failed",
                duration=duration,
                stdout="",
                stderr=f"OS error running test: {e}",
                exit_code=-1,
            )
