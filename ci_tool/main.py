"""CI tool entry point with burn-in lifecycle management subcommands.

Provides burn-in, deflake, test-status, and re-judge subcommands for
managing test maturity states and judgement re-evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from orchestrator.lifecycle.status import StatusFile


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="CI tool for test set burn-in lifecycle management"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

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
                sf.set_test_state(test_name, "burning_in", runs=0, passes=0)
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
                entry = sf.get_test_entry(name)
                runs = entry["runs"] if entry else 0
                passes = entry["passes"] if entry else 0
                print(f"  {name}: {runs} runs, {passes} passes")
        else:
            print("No tests in burning_in state")
        return 0


def cmd_deflake(args: argparse.Namespace) -> int:
    """Handle deflake subcommand.

    Transitions flaky tests back to burning_in with reset counters.

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    sf = StatusFile(args.status_file)
    errors = False

    for test_name in args.tests:
        current_state = sf.get_test_state(test_name)

        if current_state == "flaky":
            sf.set_test_state(test_name, "burning_in", runs=0, passes=0)
            print(f"  {test_name}: flaky -> burning_in (counters reset)")
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
        runs = entry.get("runs", 0)
        passes = entry.get("passes", 0)
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


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    if args.command is None:
        parse_args(["--help"])
        return 1

    if args.command == "burn-in":
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
