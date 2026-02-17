"""Entry point for the test set orchestrator.

Parses command-line arguments and orchestrates test execution based on the
mode and manifest. Supports diagnostic and detection modes, with an optional
regression flag to filter tests by co-occurrence analysis.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from orchestrator.execution.dag import TestDAG
from orchestrator.execution.executor import AsyncExecutor, SequentialExecutor
from orchestrator.reporting.html_reporter import write_html_report
from orchestrator.reporting.reporter import Reporter


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
        choices=["diagnostic", "detection"],
        default="diagnostic",
        help="Execution mode (default: diagnostic)",
    )
    parser.add_argument(
        "--regression",
        action="store_true",
        default=False,
        help="Enable regression option: select a subset of pre-existing tests by co-occurrence analysis",
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
        help="Path to write the JSON report file",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="Path to the .tests/status JSON state file",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        default=False,
        help="Allow running with uncommitted changes (commit SHA will still be recorded)",
    )

    # Regression option flags
    parser.add_argument(
        "--diff-base",
        type=str,
        default=None,
        help="Git ref to diff against for regression selection (e.g. main, HEAD~3)",
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
        help="Max fraction of stable tests to select with --regression (default: 0.10)",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=2,
        help="Max BFS hops in regression co-occurrence expansion (default: 2)",
    )

    # E-value verdict flags
    parser.add_argument(
        "--verdict",
        choices=["quick", "hifi", "off"],
        default="off",
        help="E-value test set verdict mode: quick (pool evidence across commits), "
             "hifi (current commit only, rerun until decided), off (disabled, default)",
    )
    parser.add_argument(
        "--alpha-set",
        type=float,
        default=0.05,
        help="Type I error rate for RED verdict (default: 0.05)",
    )
    parser.add_argument(
        "--beta-set",
        type=float,
        default=0.05,
        help="Type II error rate for GREEN verdict (default: 0.05)",
    )
    parser.add_argument(
        "--max-reruns",
        type=int,
        default=100,
        help="Max reruns per test for hifi verdict mode (default: 100)",
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


def _resolve_git_context(allow_dirty: bool) -> str | None:
    """Verify the working tree is clean and return the HEAD commit SHA.

    Args:
        allow_dirty: If True, skip the dirty-tree check but still return
            the commit SHA.

    Returns:
        The HEAD commit SHA, or None if git is unavailable.

    Raises:
        SystemExit: If the working tree has uncommitted changes and
            allow_dirty is False.
    """
    try:
        sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        print("Warning: git not found, commit SHA will not be recorded",
              file=sys.stderr)
        return None

    if sha_result.returncode != 0:
        print("Warning: not a git repository, commit SHA will not be recorded",
              file=sys.stderr)
        return None

    commit_sha = sha_result.stdout.strip()

    dirty_result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    is_dirty = bool(dirty_result.stdout.strip())

    if is_dirty and not allow_dirty:
        print(
            "Error: working tree has uncommitted changes.\n"
            "Commit your changes before running with --status-file so that\n"
            "test results can be attributed to a specific commit.\n"
            "Use --allow-dirty to bypass this check.",
            file=sys.stderr,
        )
        sys.exit(1)

    if is_dirty:
        commit_sha += "-dirty"

    return commit_sha


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

    # Resolve git context when status file tracking is enabled
    commit_sha: str | None = None
    if args.status_file:
        commit_sha = _resolve_git_context(args.allow_dirty)
    elif args.output:
        # Best-effort: tag report history with commit SHA without enforcing clean tree
        commit_sha = _resolve_git_context(allow_dirty=True)

    # Sync disabled state from manifest and remove disabled tests from DAG
    if args.status_file:
        from orchestrator.lifecycle.burnin import sync_disabled_state
        from orchestrator.lifecycle.status import StatusFile

        sf = StatusFile(args.status_file)
        sync_events = sync_disabled_state(dag, sf)
        if sync_events:
            print("Disabled state sync:")
            for etype, name, old_state, new_state in sync_events:
                print(f"  {name}: {old_state} \u2192 {new_state} ({etype})")
            print()

    removed = dag.remove_disabled()
    if removed:
        print(f"Disabled tests excluded from execution: {len(removed)}")
        print()

    # Handle regression option
    if args.regression:
        return _run_regression(args, manifest, dag, commit_sha)

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

    _update_status_file(results, args, commit_sha)
    verdict_data = _compute_verdict(args, dag, commit_sha)
    demoted = _print_results(results, args, commit_sha, manifest, verdict_data)
    has_failure = any(r.status == "failed" for r in results)
    return 1 if (has_failure or demoted) else 0


def _run_regression(
    args: argparse.Namespace,
    manifest: dict,
    dag: TestDAG,
    commit_sha: str | None = None,
) -> int:
    """Execute with regression option: select tests then run in chosen mode.

    Args:
        args: Parsed CLI arguments.
        manifest: Parsed manifest dict.
        dag: Constructed test DAG.
        commit_sha: Resolved git commit SHA (or None).

    Returns:
        Exit code.
    """
    from orchestrator.regression.co_occurrence import load_graph
    from orchestrator.regression.regression_selector import (
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
            "Error: --regression requires --diff-base or --changed-files",
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
    print(f"Regression ({args.mode}): {len(selection.selected_tests)} tests selected "
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

    # Execute in the chosen mode (diagnostic or detection)
    executor: SequentialExecutor | AsyncExecutor
    if args.max_parallel == 1:
        executor = SequentialExecutor(
            filtered_dag,
            mode=args.mode,
            max_failures=args.max_failures,
        )
    else:
        executor = AsyncExecutor(
            filtered_dag,
            mode=args.mode,
            max_failures=args.max_failures,
            max_parallel=args.max_parallel,
        )

    try:
        results = executor.execute()
    except ValueError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        return 1

    _update_status_file(results, args, commit_sha)
    verdict_data = _compute_verdict(args, filtered_dag, commit_sha)
    demoted = _print_results(results, args, commit_sha, filtered_manifest, verdict_data)
    has_failure = any(r.status == "failed" for r in results)
    return 1 if (has_failure or demoted) else 0


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


def _compute_verdict(
    args: argparse.Namespace,
    dag: TestDAG,
    commit_sha: str | None,
) -> dict[str, Any] | None:
    """Compute E-value test set verdict if --verdict is enabled.

    Returns:
        Verdict dict for the reporter, or None if disabled.
    """
    if args.verdict == "off" or not args.status_file:
        return None

    from orchestrator.lifecycle.e_values import (
        HiFiEvaluator,
        evaluate_test_set,
        verdict_to_dict,
    )
    from orchestrator.lifecycle.status import StatusFile

    sf = StatusFile(args.status_file)
    test_names = list(dag.nodes.keys())

    if args.verdict == "quick":
        verdict = evaluate_test_set(
            test_names, sf,
            mode="quick",
            alpha_set=args.alpha_set,
            beta_set=args.beta_set,
        )
        verdict_data = verdict_to_dict(verdict)
    else:
        # hifi
        if commit_sha is None:
            print("Warning: --verdict=hifi requires git context; skipping verdict",
                  file=sys.stderr)
            return None
        evaluator = HiFiEvaluator(
            dag, sf,
            commit_sha=commit_sha,
            alpha_set=args.alpha_set,
            beta_set=args.beta_set,
            max_reruns=args.max_reruns,
        )
        hifi_result = evaluator.evaluate(test_names)
        verdict_data = verdict_to_dict(hifi_result.verdict)
        verdict_data["total_reruns"] = hifi_result.total_reruns
        verdict_data["decided"] = hifi_result.decided

    # Print verdict summary
    print(f"\nTest Set Verdict: {verdict_data['verdict']}")
    print(f"  E_set = {verdict_data['e_set']:.4f} "
          f"(RED threshold: {verdict_data['red_threshold']:.4f})")
    print(f"  min(S_i) = {verdict_data['min_s_value']:.4f} "
          f"(GREEN threshold: {verdict_data['green_threshold']:.4f})")
    if verdict_data.get("weakest_test"):
        print(f"  Weakest test: {verdict_data['weakest_test']}")
    if "total_reruns" in verdict_data:
        print(f"  HiFi reruns: {verdict_data['total_reruns']}")

    return verdict_data


def _update_status_file(
    results: list, args: argparse.Namespace, commit_sha: str | None
) -> None:
    """Update the status file with test results if --status-file is set."""
    if not args.status_file:
        return

    from orchestrator.lifecycle.burnin import process_results
    from orchestrator.lifecycle.status import StatusFile

    sf = StatusFile(args.status_file)
    events = process_results(results, sf, commit_sha=commit_sha)
    if events:
        print("\nLifecycle events:")
        for etype, name, old_state, new_state in events:
            print(f"  {name}: {old_state} \u2192 {new_state} ({etype})")


def _print_results(
    results: list, args: argparse.Namespace,
    commit_sha: str | None = None,
    manifest: dict | None = None,
    verdict_data: dict[str, Any] | None = None,
) -> list[str]:
    """Print test execution results summary.

    Returns:
        List of test names that were reliability-demoted to flaky.
    """
    mode_label = args.mode
    if args.regression:
        mode_label += " + regression"
    print(f"Mode: {mode_label}")
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

    # Generate reports
    if args.output:
        reporter = Reporter()
        if manifest is not None:
            reporter.set_manifest(manifest)
        reporter.add_results(results)
        if commit_sha:
            reporter.set_commit_hash(commit_sha)

        if verdict_data:
            reporter.set_e_value_verdict(verdict_data)

        # Feed lifecycle data from status file to reporter
        if args.status_file and args.status_file.exists():
            from orchestrator.lifecycle.status import StatusFile

            sf = StatusFile(args.status_file)
            lifecycle_data: dict[str, dict[str, Any]] = {}
            for test_name, entry in sf.get_all_tests().items():
                lifecycle_data[test_name] = {
                    "state": entry.get("state", "new"),
                }
            reporter.set_lifecycle_data(lifecycle_data)
            reporter.set_lifecycle_config({
                "min_reliability": sf.min_reliability,
                "statistical_significance": sf.statistical_significance,
            })

        # Use history-aware generation so the HTML timeline accumulates
        existing = args.output if args.output.exists() else None
        report_data = reporter.generate_report_with_history(existing)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report_data, f, indent=2)
        print(f"Report written to: {args.output}")

        html_path = args.output.with_suffix(".html")
        write_html_report(report_data, html_path)
        print(f"HTML report written to: {html_path}")

        if reporter.reliability_demoted_tests:
            print(f"\nReliability demotions ({len(reporter.reliability_demoted_tests)}):")
            for name in reporter.reliability_demoted_tests:
                print(f"  {name}: reliability below threshold")

        return reporter.reliability_demoted_tests

    return []


if __name__ == "__main__":
    sys.exit(main())
