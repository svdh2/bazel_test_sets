"""Entry point for the test set orchestrator.

Parses command-line arguments and orchestrates test execution based on the
mode and manifest. Supports diagnostic, detection, and regression modes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from orchestrator.dag import TestDAG
from orchestrator.executor import AsyncExecutor, SequentialExecutor
from orchestrator.reporter import Reporter


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Test set orchestrator - executes tests in DAG order"
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to the JSON manifest file",
    )
    parser.add_argument(
        "--mode",
        choices=["diagnostic", "detection", "regression"],
        default="diagnostic",
        help="Execution mode (default: diagnostic)",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        help="Maximum parallel test executions (default: CPU count)",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=None,
        help="Stop after this many failures (default: unlimited)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the YAML report file",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="Path to the .tests/status JSON state file",
    )

    # Regression mode flags
    parser.add_argument(
        "--diff-base",
        type=str,
        default=None,
        help="Git ref to diff against for regression mode (e.g. main, HEAD~3)",
    )
    parser.add_argument(
        "--changed-files",
        type=str,
        default=None,
        help="Comma-separated list of changed files (alternative to --diff-base)",
    )
    parser.add_argument(
        "--co-occurrence-graph",
        type=Path,
        default=Path(".tests/co_occurrence_graph.json"),
        help="Path to co-occurrence graph JSON (default: .tests/co_occurrence_graph.json)",
    )
    parser.add_argument(
        "--max-test-percentage",
        type=float,
        default=0.10,
        help="Max fraction of stable tests to select in regression mode (default: 0.10)",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=2,
        help="Max BFS hops in regression co-occurrence expansion (default: 2)",
    )

    return parser.parse_args(argv)


def _get_changed_files(diff_base: str) -> list[str]:
    """Get changed files from git diff.

    Args:
        diff_base: Git ref to diff against.

    Returns:
        List of changed file paths.

    Raises:
        RuntimeError: If git diff fails.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{diff_base}...HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("git not found")

    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")

    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    # Load manifest
    try:
        manifest = json.loads(args.manifest.read_text())
    except FileNotFoundError:
        print(f"Error: Manifest file not found: {args.manifest}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in manifest: {e}", file=sys.stderr)
        return 1

    # Build DAG
    try:
        dag = TestDAG.from_manifest(manifest)
    except ValueError as e:
        print(f"Error building DAG: {e}", file=sys.stderr)
        return 1

    # Handle regression mode
    if args.mode == "regression":
        return _run_regression_mode(args, manifest, dag)

    # Execute tests (use AsyncExecutor for parallel, SequentialExecutor as fallback)
    executor: SequentialExecutor | AsyncExecutor
    if args.max_parallel == 1:
        executor = SequentialExecutor(
            dag,
            mode=args.mode,
            max_failures=args.max_failures,
        )
    else:
        executor = AsyncExecutor(
            dag,
            mode=args.mode,
            max_failures=args.max_failures,
            max_parallel=args.max_parallel,
        )

    try:
        results = executor.execute()
    except ValueError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        return 1

    _print_results(results, args)
    return 1 if any(r.status == "failed" for r in results) else 0


def _run_regression_mode(
    args: argparse.Namespace,
    manifest: dict,
    dag: TestDAG,
) -> int:
    """Execute regression mode: select tests then run them.

    Args:
        args: Parsed CLI arguments.
        manifest: Parsed manifest dict.
        dag: Constructed test DAG.

    Returns:
        Exit code.
    """
    from orchestrator.co_occurrence import load_graph
    from orchestrator.regression_selector import (
        RegressionConfig,
        select_regression_tests,
    )

    # Load co-occurrence graph
    graph = load_graph(args.co_occurrence_graph)
    if graph is None:
        print(
            f"Error: Co-occurrence graph not found: {args.co_occurrence_graph}\n"
            f"Run 'ci build-graph' first to build the co-occurrence graph.",
            file=sys.stderr,
        )
        return 1

    # Determine changed files
    if args.changed_files is not None:
        changed_files = [
            f.strip() for f in args.changed_files.split(",") if f.strip()
        ]
    elif args.diff_base:
        try:
            changed_files = _get_changed_files(args.diff_base)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    else:
        print(
            "Error: Regression mode requires --diff-base or --changed-files",
            file=sys.stderr,
        )
        return 1

    if not changed_files:
        print("No changed files detected. No tests to run.")
        return 0

    # Configure and run regression selection
    config = RegressionConfig(
        max_test_percentage=args.max_test_percentage,
        max_hops=args.max_hops,
    )

    selection = select_regression_tests(
        changed_files=changed_files,
        graph=graph,
        manifest=manifest,
        config=config,
    )

    # Print selection summary
    print(f"Regression mode: {len(selection.selected_tests)} tests selected "
          f"from {selection.total_stable_tests} stable tests "
          f"({len(changed_files)} files changed)")
    if selection.fallback_used:
        print("  (fallback: co-occurrence yielded too few tests)")
    print()

    if not selection.selected_tests:
        print("No tests selected for regression run.")
        return 0

    # Build a filtered DAG with only selected tests
    filtered_manifest = _filter_manifest(manifest, selection.selected_tests)
    try:
        filtered_dag = TestDAG.from_manifest(filtered_manifest)
    except ValueError as e:
        print(f"Error building filtered DAG: {e}", file=sys.stderr)
        return 1

    # Execute in diagnostic order
    executor: SequentialExecutor | AsyncExecutor
    if args.max_parallel == 1:
        executor = SequentialExecutor(
            filtered_dag,
            mode="diagnostic",
            max_failures=args.max_failures,
        )
    else:
        executor = AsyncExecutor(
            filtered_dag,
            mode="diagnostic",
            max_failures=args.max_failures,
            max_parallel=args.max_parallel,
        )

    try:
        results = executor.execute()
    except ValueError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        return 1

    _print_results(results, args)
    return 1 if any(r.status == "failed" for r in results) else 0


def _filter_manifest(
    manifest: dict,
    selected_tests: list[str],
) -> dict:
    """Create a filtered manifest containing only selected tests.

    Args:
        manifest: Full manifest dict.
        selected_tests: List of test labels to include.

    Returns:
        Filtered manifest dict.
    """
    selected_set = set(selected_tests)
    test_set_tests = manifest.get("test_set_tests", {})

    filtered_tests = {}
    for name, data in test_set_tests.items():
        if name in selected_set:
            # Filter depends_on to only include selected tests
            filtered_data = dict(data)
            filtered_data["depends_on"] = [
                dep for dep in data.get("depends_on", [])
                if dep in selected_set
            ]
            filtered_tests[name] = filtered_data

    return {
        "test_set": manifest.get("test_set", {}),
        "test_set_tests": filtered_tests,
    }


def _print_results(results: list, args: argparse.Namespace) -> None:
    """Print test execution results summary."""
    print(f"Mode: {args.mode}")
    print(f"Tests executed: {len(results)}")
    print()

    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    dep_failed = sum(1 for r in results if r.status == "dependencies_failed")

    for r in results:
        status_icon = {
            "passed": "PASS",
            "failed": "FAIL",
            "dependencies_failed": "SKIP",
        }
        icon = status_icon.get(r.status, r.status.upper())
        print(f"  [{icon}] {r.name} - {r.assertion} ({r.duration:.2f}s)")
        if r.status == "failed" and r.stderr:
            for line in r.stderr.strip().splitlines():
                print(f"         {line}")

    print()
    print(f"Results: {passed} passed, {failed} failed, {dep_failed} skipped")

    # Generate report
    if args.output:
        reporter = Reporter()
        reporter.add_results(results)
        reporter.write_yaml(args.output)
        print(f"Report written to: {args.output}")


if __name__ == "__main__":
    sys.exit(main())
