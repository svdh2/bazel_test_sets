"""Entry point for the test set orchestrator and lifecycle management.

Parses command-line arguments and orchestrates test execution based on the
mode and manifest. Supports diagnostic and detection modes, with an optional
effort flag to control test execution thoroughness.

Also provides lifecycle subcommands: burn-in, deflake, test-status, re-judge,
and build-graph for managing test maturity states and judgement re-evaluation.
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
from orchestrator.lifecycle.status import StatusFile, runs_and_passes_from_history
from orchestrator.reporting.html_reporter import write_html_report
from orchestrator.reporting.reporter import Reporter


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Test set orchestrator - executes tests in DAG order"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to the JSON manifest file",
    )
    parser.add_argument(
        "--mode",
        choices=["diagnostic", "detection"],
        default="diagnostic",
        help="Execution mode (default: diagnostic)",
    )
    parser.add_argument(
        "--effort",
        choices=["regression", "converge", "max"],
        default=None,
        help="Effort mode: regression (co-occurrence selection, quick verdict), "
             "converge (SPRT reruns on failures, hifi verdict), "
             "max (SPRT reruns on all tests, hifi verdict)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Path to write the JSON report file",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        default=False,
        help="Allow running with uncommitted changes (commit SHA will still be recorded)",
    )
    parser.add_argument(
        "--discover-workspace-tests",
        action="store_true",
        default=False,
        help="Discover all test_set_test targets in the workspace via bazel query "
             "and include them in the report (requires BUILD_WORKSPACE_DIRECTORY)",
    )

    # Execution tuning flags (ci_gate parameters)
    parser.add_argument(
        "--max-reruns",
        type=int,
        default=100,
        help="Maximum SPRT reruns per test (default: 100)",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=None,
        help="Stop after N failures (detection mode)",
    )
    parser.add_argument(
        "--max-parallel",
        type=int,
        default=None,
        help="Maximum parallel test executions",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=None,
        help="Path to the status file (enables lifecycle features)",
    )
    parser.add_argument(
        "--max-test-percentage",
        type=float,
        default=0.10,
        help="Regression: max fraction of tests to select (default: 0.10)",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=2,
        help="Regression: max BFS hops in co-occurrence graph (default: 2)",
    )
    parser.add_argument(
        "--skip-unchanged",
        action="store_true",
        dest="skip_unchanged",
        default=True,
        help="Skip hash-unchanged tests with conclusive SPRT (default: True)",
    )
    parser.add_argument(
        "--no-skip-unchanged",
        action="store_false",
        dest="skip_unchanged",
        help="Do not skip unchanged tests",
    )
    parser.add_argument(
        "--min-reliability",
        type=float,
        default=0.99,
        help="SPRT: minimum reliability threshold (default: 0.99)",
    )
    parser.add_argument(
        "--statistical-significance",
        type=float,
        default=0.95,
        help="SPRT: significance level for decisions (default: 0.95)",
    )
    parser.add_argument(
        "--flaky-deadline-days",
        type=int,
        default=14,
        help="Days before flaky tests auto-disable (default: 14)",
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

    # Lifecycle subcommands
    subparsers = parser.add_subparsers(dest="command")

    # burn-in subcommand
    burn_in_parser = subparsers.add_parser(
        "burn-in",
        help="Transition tests from new to burning_in and run burn-in sweep",
    )
    burn_in_parser.add_argument(
        "--status-file",
        type=Path,
        default=Path(".tests/status"),
        help="Path to the status file (default: .tests/status)",
    )
    burn_in_parser.add_argument(
        "tests",
        nargs="*",
        help="Test paths to burn in (if empty, runs all burning_in tests)",
    )

    # deflake subcommand
    deflake_parser = subparsers.add_parser(
        "deflake",
        help="Transition flaky tests back to burning_in with reset counters",
    )
    deflake_parser.add_argument(
        "--status-file",
        type=Path,
        default=Path(".tests/status"),
        help="Path to the status file (default: .tests/status)",
    )
    deflake_parser.add_argument(
        "tests",
        nargs="+",
        help="Test paths to deflake",
    )

    # test-status subcommand
    status_parser = subparsers.add_parser(
        "test-status",
        help="Display status of all tests",
    )
    status_parser.add_argument(
        "--status-file",
        type=Path,
        default=Path(".tests/status"),
        help="Path to the status file (default: .tests/status)",
    )
    status_parser.add_argument(
        "--state",
        choices=["new", "burning_in", "stable", "flaky", "disabled"],
        default=None,
        help="Filter by state",
    )

    # re-judge subcommand
    rejudge_parser = subparsers.add_parser(
        "re-judge",
        help="Re-evaluate judgements using stored measurements",
    )
    rejudge_parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="Path to the JSON manifest file",
    )
    rejudge_parser.add_argument(
        "--measurements-dir",
        type=Path,
        default=Path(".tests/measurements"),
        help="Directory containing stored measurements (default: .tests/measurements)",
    )
    rejudge_parser.add_argument(
        "tests",
        nargs="*",
        help="Test paths to re-judge (if empty, re-judges all eligible tests)",
    )

    # build-graph subcommand
    build_graph_parser = subparsers.add_parser(
        "build-graph",
        help="Build/update the co-occurrence graph from git history",
    )
    build_graph_parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tests/co_occurrence_graph.json"),
        help="Output path for the graph (default: .tests/co_occurrence_graph.json)",
    )
    build_graph_parser.add_argument(
        "--source-extensions",
        type=str,
        default=None,
        help="Comma-separated source file extensions (e.g. .py,.java,.go)",
    )
    build_graph_parser.add_argument(
        "--test-patterns",
        type=str,
        default=None,
        help="Comma-separated test file patterns (e.g. *_test.*,test_*.*)",
    )
    build_graph_parser.add_argument(
        "--max-history",
        type=int,
        default=10000,
        help="Maximum number of git commits to analyze (default: 10000)",
    )
    build_graph_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Path to the git repository root (default: current directory)",
    )

    return parser.parse_args(argv)


# --- Lifecycle subcommand handlers ---


def cmd_burn_in(args: argparse.Namespace) -> int:
    """Handle burn-in subcommand.

    Transitions specified tests from new to burning_in. If no tests
    are specified, lists all burning_in tests.

    Returns:
        Exit code (0 for success).
    """
    sf = StatusFile(args.status_file)

    if args.tests:
        transitioned = []
        for test_name in args.tests:
            current_state = sf.get_test_state(test_name)

            if current_state == "burning_in":
                print(f"  {test_name}: already burning_in")
                continue

            if current_state == "disabled":
                print(
                    f"  {test_name}: cannot burn-in from state 'disabled' "
                    f"(remove disabled=True from BUILD file first)",
                    file=sys.stderr,
                )
                continue

            if current_state is None or current_state == "new":
                sf.set_test_state(test_name, "burning_in", clear_history=True)
                transitioned.append(test_name)
                print(f"  {test_name}: {current_state or 'new'} -> burning_in")
            else:
                print(
                    f"  {test_name}: cannot burn-in from state '{current_state}' "
                    f"(use 'deflake' for flaky tests)",
                    file=sys.stderr,
                )

        sf.save()

        if transitioned:
            print(f"\nTransitioned {len(transitioned)} test(s) to burning_in")
            print("Run burn-in sweep with the orchestrator to evaluate them")
        return 0
    else:
        # List all burning_in tests
        burning_in = sf.get_tests_by_state("burning_in")
        if burning_in:
            print(f"Tests in burning_in state ({len(burning_in)}):")
            for name in sorted(burning_in):
                history = sf.get_test_history(name)
                runs, passes = runs_and_passes_from_history(history)
                print(f"  {name}: {runs} runs, {passes} passes")
        else:
            print("No tests in burning_in state")
        return 0


def cmd_deflake(args: argparse.Namespace) -> int:
    """Handle deflake subcommand.

    Transitions flaky tests back to burning_in with cleared history.

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    sf = StatusFile(args.status_file)
    errors = False

    for test_name in args.tests:
        current_state = sf.get_test_state(test_name)

        if current_state == "flaky":
            sf.set_test_state(test_name, "burning_in", clear_history=True)
            print(f"  {test_name}: flaky -> burning_in (history reset)")
        elif current_state is None:
            print(
                f"  {test_name}: not found in status file",
                file=sys.stderr,
            )
            errors = True
        elif current_state == "disabled":
            print(
                f"  {test_name}: cannot deflake from state 'disabled' "
                f"(remove disabled=True from BUILD file first)",
                file=sys.stderr,
            )
            errors = True
        else:
            print(
                f"  {test_name}: cannot deflake from state '{current_state}' "
                f"(must be 'flaky')",
                file=sys.stderr,
            )
            errors = True

    sf.save()
    return 1 if errors else 0


def cmd_test_status(args: argparse.Namespace) -> int:
    """Handle test-status subcommand.

    Displays status of all tests in tabular format.

    Returns:
        Exit code (0 for success).
    """
    sf = StatusFile(args.status_file)
    all_tests = sf.get_all_tests()

    if args.state:
        all_tests = {
            name: entry
            for name, entry in all_tests.items()
            if entry.get("state") == args.state
        }

    if not all_tests:
        print("No tests found")
        return 0

    # Compute column widths
    name_width = max(len(name) for name in all_tests)
    name_width = max(name_width, 4)  # minimum "Test"

    # Print header
    header = f"{'Test':<{name_width}}  {'State':<12}  {'Runs':>6}  {'Passes':>6}  {'Last Updated'}"
    print(header)
    print("-" * len(header))

    # Print rows
    for name in sorted(all_tests):
        entry = all_tests[name]
        state = entry.get("state", "unknown")
        history = entry.get("history", [])
        runs, passes = runs_and_passes_from_history(history)
        last_updated = entry.get("last_updated", "N/A")
        # Truncate timestamp to date portion if ISO format
        if "T" in str(last_updated):
            last_updated = str(last_updated).split("T")[0]

        print(
            f"{name:<{name_width}}  {state:<12}  {runs:>6}  {passes:>6}  {last_updated}"
        )

    # Print summary
    print()
    state_counts: dict[str, int] = {}
    for entry in all_tests.values():
        state = entry.get("state", "unknown")
        state_counts[state] = state_counts.get(state, 0) + 1

    parts = [f"{count} {state}" for state, count in sorted(state_counts.items())]
    print(f"Total: {len(all_tests)} tests ({', '.join(parts)})")

    return 0


def cmd_re_judge(args: argparse.Namespace) -> int:
    """Handle re-judge subcommand.

    Re-evaluates judgements using stored measurements. If specific tests
    are given, re-judges those tests. Otherwise, finds and re-judges all
    eligible tests (those with judgement targets and stored measurements).

    Returns:
        Exit code (0 for success, 1 for any failures).
    """
    from orchestrator.analysis.judgement import execute_judgement, find_rejudgeable_tests

    # Load manifest
    try:
        manifest = json.loads(args.manifest.read_text())
    except FileNotFoundError:
        print(
            f"Error: Manifest file not found: {args.manifest}",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in manifest: {e}", file=sys.stderr)
        return 1

    measurements_dir = args.measurements_dir

    # Determine which tests to re-judge
    if args.tests:
        test_names = args.tests
    else:
        test_names = find_rejudgeable_tests(manifest, measurements_dir)
        if not test_names:
            print("No eligible tests found for re-judging")
            print(
                "Tests need both a judgement target and stored measurements"
            )
            return 0

    # Execute judgements
    has_failures = False
    results = []

    for test_name in test_names:
        result = execute_judgement(test_name, manifest, measurements_dir)
        results.append((test_name, result))

        status_icon = {
            "passed": "PASS",
            "failed": "FAIL",
            "skipped": "SKIP",
            "judgement_error": "ERR",
        }
        icon = status_icon.get(result.status, result.status.upper())
        reason = f" ({result.reason})" if result.reason else ""
        print(f"  [{icon}] {test_name}{reason}")

        if result.status == "failed":
            has_failures = True
        elif result.status == "judgement_error":
            has_failures = True

    # Summary
    print()
    total = len(results)
    passed = sum(1 for _, r in results if r.status == "passed")
    failed = sum(1 for _, r in results if r.status == "failed")
    skipped = sum(1 for _, r in results if r.status == "skipped")
    errors = sum(1 for _, r in results if r.status == "judgement_error")

    parts = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    if errors:
        parts.append(f"{errors} errors")

    print(f"Re-judge: {total} tests ({', '.join(parts)})")

    return 1 if has_failures else 0


def cmd_build_graph(args: argparse.Namespace) -> int:
    """Handle build-graph subcommand.

    Builds or incrementally updates the co-occurrence graph from git history.

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    from orchestrator.regression.co_occurrence import (
        build_co_occurrence_graph,
        load_graph,
        parse_git_log,
        run_git_log,
        save_graph,
    )

    # Parse source extensions and test patterns
    source_extensions = None
    if args.source_extensions:
        source_extensions = [
            ext.strip() for ext in args.source_extensions.split(",")
        ]

    test_patterns = None
    if args.test_patterns:
        test_patterns = [
            pat.strip() for pat in args.test_patterns.split(",")
        ]

    # Load existing graph for incremental update
    existing_graph = load_graph(args.output)
    since_commit = None
    if existing_graph is not None:
        since_commit = existing_graph.get("metadata", {}).get("last_commit")
        print(f"Updating existing graph (last commit: {since_commit})")
    else:
        print("Building new co-occurrence graph")

    # Run git log
    try:
        git_output = run_git_log(
            args.repo_root,
            max_history=args.max_history,
            since_commit=since_commit,
        )
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Parse commits
    commits = parse_git_log(git_output)
    print(f"Parsed {len(commits)} commits from git history")

    # Build graph
    graph = build_co_occurrence_graph(
        commits,
        source_extensions=source_extensions,
        test_patterns=test_patterns,
        existing_graph=existing_graph,
    )

    # Save graph
    save_graph(graph, args.output)

    total = graph["metadata"]["total_commits_analyzed"]
    files = len(graph["file_commits"])
    print(f"Graph saved to {args.output}")
    print(f"  Total commits analyzed: {total}")
    print(f"  Total files indexed: {files}")

    return 0


# --- Hash-based filtering ---


def _compute_and_filter_hashes(
    dag: TestDAG,
    sf: StatusFile,
    *,
    skip_unchanged: bool = True,
) -> tuple[set[str], set[str], dict[str, str]]:
    """Compute target hashes, compare with stored hashes, and filter tests.

    1. Calls ``compute_target_hashes`` for all test labels in the DAG.
    2. Compares each hash against the value stored in the status file.
    3. For changed hashes: calls ``sf.invalidate_evidence(test_name)`` and
       updates the stored hash.
    4. For unchanged hashes: marks the test as skippable (if the test has
       a conclusive SPRT decision in same-hash history).
    5. Updates stored hashes in the status file and saves.

    Args:
        dag: Test DAG containing all test labels.
        sf: StatusFile instance for reading/writing hashes.
        skip_unchanged: If True, tests with unchanged hashes and conclusive
            SPRT may be skipped.

    Returns:
        Tuple of (hash_changed_tests, hash_unchanged_skippable, target_hashes).
        - hash_changed_tests: set of test names whose hash changed or is new.
        - hash_unchanged_skippable: set of test names that can be skipped
          (only populated when skip_unchanged is True).
        - target_hashes: mapping of test name to current hash value.
    """
    from orchestrator.execution.target_hash import compute_target_hashes

    test_labels = list(dag.nodes.keys())
    target_hashes = compute_target_hashes(test_labels)

    if not target_hashes:
        # Hash computation failed (Bazel not available, timeout, etc.)
        # Fall back to no filtering -- treat all as changed
        print(
            "Warning: target hash computation failed, "
            "skipping hash-based filtering",
            file=sys.stderr,
        )
        return set(test_labels), set(), {}

    hash_changed: set[str] = set()
    hash_unchanged_skippable: set[str] = set()

    for label in test_labels:
        current_hash = target_hashes.get(label)
        if current_hash is None:
            # No hash available for this target -- treat as changed
            hash_changed.add(label)
            continue

        stored_hash = sf.get_target_hash(label)

        if stored_hash is None or stored_hash != current_hash:
            # Hash is new or changed -- invalidate evidence
            hash_changed.add(label)
            if stored_hash is not None:
                sf.invalidate_evidence(label)
            sf.set_target_hash(label, current_hash)
        else:
            # Hash unchanged -- check if skippable
            if skip_unchanged:
                # A test is skippable if it has a conclusive SPRT decision
                # in its same-hash history.  For simplicity, a non-empty
                # same-hash history with a stable/flaky state indicates
                # conclusiveness.
                state = sf.get_test_state(label)
                if state in ("stable", "flaky", "disabled"):
                    hash_unchanged_skippable.add(label)
                # burning_in / new tests are NOT skippable -- they need
                # more evidence regardless of hash.

    sf.save()

    # Print summary
    n_changed = len(hash_changed)
    n_unchanged = len(test_labels) - n_changed
    n_skipped = len(hash_unchanged_skippable)
    print(
        f"Hash filter: {n_changed} tests changed, "
        f"{n_unchanged} unchanged ({n_skipped} skippable)"
    )

    return hash_changed, hash_unchanged_skippable, target_hashes


# --- Orchestrator run helpers ---


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
            "Commit your changes before running with status_file so that\n"
            "test results can be attributed to a specific commit.\n"
            "Use --allow-dirty to bypass this check.",
            file=sys.stderr,
        )
        sys.exit(1)

    if is_dirty:
        commit_sha += "-dirty"

    return commit_sha


def _run_orchestrator(args: argparse.Namespace) -> int:
    """Run the orchestrator test execution path (default, no subcommand)."""
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

        sf = StatusFile(
            args.status_file,
            min_reliability=args.min_reliability,
            statistical_significance=args.statistical_significance,
        )
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

    # Dispatch based on effort mode
    if args.effort == "regression":
        return _run_regression(args, manifest, dag, commit_sha)
    elif args.effort in ("converge", "max"):
        return _run_effort(args, manifest, dag, commit_sha)

    # Default: run all tests once, no verdict
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
    demoted = _print_results(results, args, commit_sha, manifest)
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
            "Error: --effort regression requires --diff-base or --changed-files",
            file=sys.stderr,
        )
        return 1

    if not changed_files:
        print("No changed files detected. No tests to run.")
        return 0

    # Compute target hashes (if status_file is configured)
    target_hashes: dict[str, str] = {}
    hash_changed_tests: set[str] = set()
    if args.status_file:
        sf = StatusFile(
            args.status_file,
            min_reliability=args.min_reliability,
            statistical_significance=args.statistical_significance,
        )
        hash_changed_tests, _, target_hashes = _compute_and_filter_hashes(
            dag, sf, skip_unchanged=args.skip_unchanged,
        )

    # Configure and run regression selection
    regression_config = RegressionConfig(
        max_test_percentage=args.max_test_percentage,
        max_hops=args.max_hops,
    )

    selection = select_regression_tests(
        changed_files=changed_files,
        graph=graph,
        manifest=manifest,
        config=regression_config,
    )

    selected = list(selection.selected_tests)

    # If we have hash information, intersect selected stable tests with
    # hash-changed tests.  This skips tests that were selected by
    # co-occurrence but whose target inputs haven't actually changed.
    if hash_changed_tests and args.skip_unchanged:
        before_count = len(selected)
        selected = [
            t for t in selected
            if t in hash_changed_tests
        ]
        hash_filtered = before_count - len(selected)
        if hash_filtered > 0:
            print(
                f"Hash filter removed {hash_filtered} unchanged tests "
                f"from regression selection"
            )

    # Print selection summary
    print(f"Regression ({args.mode}): {len(selected)} tests selected "
          f"from {selection.total_stable_tests} stable tests "
          f"({len(changed_files)} files changed)")
    if selection.fallback_used:
        print("  (fallback: co-occurrence yielded too few tests)")
    print()

    if not selected:
        print("No tests selected for regression run.")
        return 0

    # Build a filtered DAG with only selected tests
    filtered_manifest = _filter_manifest(manifest, selected)
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
    demoted = _print_results(
        results, args, commit_sha, manifest, verdict_data,
    )
    has_failure = any(r.status == "failed" for r in results)
    return 1 if (has_failure or demoted) else 0


def _run_effort(
    args: argparse.Namespace,
    manifest: dict,
    dag: TestDAG,
    commit_sha: str | None = None,
) -> int:
    """Execute with effort converge/max: run tests then SPRT-rerun for classification.

    Args:
        args: Parsed CLI arguments.
        manifest: Parsed manifest dict.
        dag: Constructed test DAG.
        commit_sha: Resolved git commit SHA (or None).

    Returns:
        Exit code.
    """
    from orchestrator.execution.effort import EffortRunner

    if not args.status_file:
        print(
            "Error: --effort converge/max requires --status-file",
            file=sys.stderr,
        )
        return 1

    if commit_sha is None:
        commit_sha = _resolve_git_context(args.allow_dirty)
        if commit_sha is None:
            print(
                "Error: --effort converge/max requires git context",
                file=sys.stderr,
            )
            return 1

    sf = StatusFile(
        args.status_file,
        min_reliability=args.min_reliability,
        statistical_significance=args.statistical_significance,
    )

    # Hash-based filtering
    target_hashes: dict[str, str] = {}
    if args.skip_unchanged:
        hash_changed, hash_skippable, target_hashes = (
            _compute_and_filter_hashes(dag, sf, skip_unchanged=True)
        )
        if hash_skippable:
            # Remove skippable tests from the DAG
            for label in hash_skippable:
                if label in dag.nodes:
                    dag.nodes.pop(label, None)
            print(
                f"Skipped {len(hash_skippable)} unchanged tests "
                f"with conclusive SPRT"
            )
            print()
    else:
        # Even without skip_unchanged, compute hashes for evidence pooling
        _, _, target_hashes = _compute_and_filter_hashes(
            dag, sf, skip_unchanged=False,
        )

    # Phase 1: Execute all tests once
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
        initial_results = executor.execute()
    except ValueError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        return 1

    # Record initial results in status file
    for r in initial_results:
        if r.status == "dependencies_failed":
            continue
        passed = r.status == "passed"
        sf.record_run(
            r.name, passed, commit=commit_sha,
            target_hash=target_hashes.get(r.name),
        )
    sf.save()

    # Phase 2: SPRT rerun loop
    runner = EffortRunner(
        dag=dag,
        status_file=sf,
        commit_sha=commit_sha,
        max_reruns=args.max_reruns,
        effort_mode=args.effort,
        initial_results=initial_results,
    )
    effort_result = runner.run()

    # Phase 3: Verdict
    verdict_data = _compute_verdict(args, dag, commit_sha)

    # Phase 4: Print results
    _print_effort_results(
        initial_results, effort_result, args, commit_sha, manifest,
        verdict_data,
    )

    # Exit code: 1 if any true_fail or flake
    has_bad = any(
        c.classification in ("true_fail", "flake")
        for c in effort_result.classifications.values()
    )
    return 1 if has_bad else 0


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
    """Compute E-value test set verdict based on effort mode.

    Verdict mode is implied by --effort:
      - None (no effort): no verdict
      - regression: quick (pool evidence across commits)
      - converge/max: hifi (current commit only, rerun until decided)

    Returns:
        Verdict dict for the reporter, or None if disabled.
    """
    if args.effort is None or not args.status_file:
        return None

    from orchestrator.lifecycle.e_values import (
        HiFiEvaluator,
        evaluate_test_set,
        verdict_to_dict,
    )

    sf = StatusFile(
        args.status_file,
        min_reliability=args.min_reliability,
        statistical_significance=args.statistical_significance,
    )
    test_names = list(dag.nodes.keys())

    alpha_set = 0.05
    beta_set = 0.05

    if args.effort == "regression":
        verdict = evaluate_test_set(
            test_names, sf,
            mode="quick",
            alpha_set=alpha_set,
            beta_set=beta_set,
        )
        verdict_data = verdict_to_dict(verdict)
    else:
        # converge / max -> hifi
        if commit_sha is None:
            print("Warning: hifi verdict requires git context; skipping verdict",
                  file=sys.stderr)
            return None
        evaluator = HiFiEvaluator(
            dag, sf,
            commit_sha=commit_sha,
            alpha_set=alpha_set,
            beta_set=beta_set,
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
    """Update the status file with test results if status_file is configured."""
    if not args.status_file:
        return

    from orchestrator.lifecycle.burnin import process_results

    sf = StatusFile(
        args.status_file,
        min_reliability=args.min_reliability,
        statistical_significance=args.statistical_significance,
    )
    events = process_results(results, sf, commit_sha=commit_sha)
    if events:
        print("\nLifecycle events:")
        for etype, name, old_state, new_state in events:
            print(f"  {name}: {old_state} \u2192 {new_state} ({etype})")


def _discover_and_merge(manifest: dict) -> dict:
    """Run workspace discovery and merge results into manifest for reporting.

    Returns the original manifest if discovery fails or finds nothing new.
    """
    from orchestrator.discovery.workspace import (
        discover_workspace_tests,
        merge_discovered_tests,
    )

    discovered = discover_workspace_tests()
    if discovered is None:
        return manifest

    merged = merge_discovered_tests(manifest, discovered)

    original_count = len(manifest.get("test_set_tests", {}))
    merged_count = len(merged.get("test_set_tests", {}))
    new_count = merged_count - original_count

    if new_count > 0:
        print(f"Workspace discovery: {new_count} additional test(s) found")

    return merged


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
    if args.effort:
        mode_label += f" + effort:{args.effort}"
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
        from orchestrator.reporting.source_links import resolve_source_link_base

        reporter = Reporter()
        reporting_manifest = manifest
        if manifest is not None and args.discover_workspace_tests:
            reporting_manifest = _discover_and_merge(manifest)
        if reporting_manifest is not None:
            reporter.set_manifest(reporting_manifest)
        reporter.add_results(results)
        if commit_sha:
            reporter.set_commit_hash(commit_sha)
        reporter.set_source_link_base(resolve_source_link_base(commit_sha))

        if verdict_data:
            reporter.set_e_value_verdict(verdict_data)

        # Feed lifecycle data from status file to reporter
        if args.status_file and args.status_file.exists():
            sf = StatusFile(
                args.status_file,
                min_reliability=args.min_reliability,
                statistical_significance=args.statistical_significance,
            )
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


def _print_effort_results(
    initial_results: list,
    effort_result: Any,
    args: argparse.Namespace,
    commit_sha: str | None = None,
    manifest: dict | None = None,
    verdict_data: dict[str, Any] | None = None,
) -> None:
    """Print effort mode results with per-test SPRT classifications."""
    mode_label = f"{args.mode} + effort:{args.effort}"
    print(f"Mode: {mode_label}")
    print(f"Tests executed: {len(initial_results)} (initial), "
          f"{effort_result.total_reruns} reruns")
    print()

    _CLASSIFICATION_ICONS = {
        "true_pass": "TRUE_PASS",
        "true_fail": "TRUE_FAIL",
        "flake": "FLAKE",
        "undecided": "UNDECIDED",
    }

    true_pass = 0
    true_fail = 0
    flake = 0
    undecided = 0
    skipped = 0

    for r in initial_results:
        if r.status == "dependencies_failed":
            skipped += 1
            print(f"  [SKIP] {r.name} - {r.assertion} (dependencies_failed)")
            continue

        c = effort_result.classifications.get(r.name)
        if c is None:
            print(f"  [???] {r.name} - {r.assertion}")
            continue

        icon = _CLASSIFICATION_ICONS.get(c.classification, c.classification.upper())

        detail = f"{c.passes}/{c.runs} passed"
        if c.sprt_decision not in ("not_evaluated",):
            detail += f", SPRT: {c.sprt_decision}"

        print(f"  [{icon}] {r.name} - {r.assertion} ({r.duration:.2f}s, {detail})")

        if r.status == "failed" and c.classification == "true_fail" and r.stderr:
            for line in r.stderr.strip().splitlines():
                print(f"         {line}")

        if c.classification == "true_pass":
            true_pass += 1
        elif c.classification == "true_fail":
            true_fail += 1
        elif c.classification == "flake":
            flake += 1
        elif c.classification == "undecided":
            undecided += 1

    print()
    parts = []
    if true_pass:
        parts.append(f"{true_pass} true_pass")
    if true_fail:
        parts.append(f"{true_fail} true_fail")
    if flake:
        parts.append(f"{flake} flake")
    if undecided:
        parts.append(f"{undecided} undecided")
    if skipped:
        parts.append(f"{skipped} skipped")
    print(f"Results: {', '.join(parts)}")
    print(f"Total reruns: {effort_result.total_reruns} "
          f"(budget: {args.max_reruns} per test)")

    # Generate reports
    if args.output:
        from orchestrator.reporting.source_links import resolve_source_link_base

        reporter = Reporter()
        reporting_manifest = manifest
        if manifest is not None and args.discover_workspace_tests:
            reporting_manifest = _discover_and_merge(manifest)
        if reporting_manifest is not None:
            reporter.set_manifest(reporting_manifest)
        reporter.add_results(initial_results)
        if commit_sha:
            reporter.set_commit_hash(commit_sha)
        reporter.set_source_link_base(resolve_source_link_base(commit_sha))

        if verdict_data:
            reporter.set_e_value_verdict(verdict_data)

        # Add effort classifications to report
        effort_data = {
            "mode": args.effort,
            "total_reruns": effort_result.total_reruns,
            "max_reruns_per_test": args.max_reruns,
            "classifications": {
                name: {
                    "classification": c.classification,
                    "initial_status": c.initial_status,
                    "runs": c.runs,
                    "passes": c.passes,
                    "sprt_decision": c.sprt_decision,
                }
                for name, c in effort_result.classifications.items()
            },
        }
        reporter.set_effort_data(effort_data)

        if args.status_file and args.status_file.exists():
            sf = StatusFile(
                args.status_file,
                min_reliability=args.min_reliability,
                statistical_significance=args.statistical_significance,
            )
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

        existing = args.output if args.output.exists() else None
        report_data = reporter.generate_report_with_history(existing)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report_data, f, indent=2)
        print(f"Report written to: {args.output}")

        html_path = args.output.with_suffix(".html")
        write_html_report(report_data, html_path)
        print(f"HTML report written to: {html_path}")


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    if args.command is None:
        # Orchestrator run path -- validate manifest is provided
        if args.manifest is None:
            print("Error: --manifest is required", file=sys.stderr)
            return 1
        return _run_orchestrator(args)
    elif args.command == "burn-in":
        return cmd_burn_in(args)
    elif args.command == "deflake":
        return cmd_deflake(args)
    elif args.command == "test-status":
        return cmd_test_status(args)
    elif args.command == "re-judge":
        return cmd_re_judge(args)
    elif args.command == "build-graph":
        return cmd_build_graph(args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
