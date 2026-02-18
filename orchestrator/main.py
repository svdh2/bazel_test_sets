"""Entry point for the test set orchestrator.

Parses command-line arguments and orchestrates test execution based on the
mode and manifest. Supports diagnostic and detection modes, with an optional
effort flag to control test execution thoroughness.
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
from orchestrator.lifecycle.config import TestSetConfig
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
        "--config-file",
        type=Path,
        default=None,
        help="Path to the .test_set_config JSON file",
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
            "Commit your changes before running with status_file so that\n"
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

    # Load config (used for execution tuning parameters)
    config = TestSetConfig(args.config_file)

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
    if config.status_file:
        commit_sha = _resolve_git_context(args.allow_dirty)
    elif args.output:
        # Best-effort: tag report history with commit SHA without enforcing clean tree
        commit_sha = _resolve_git_context(allow_dirty=True)

    # Sync disabled state from manifest and remove disabled tests from DAG
    if config.status_file:
        from orchestrator.lifecycle.burnin import sync_disabled_state
        from orchestrator.lifecycle.status import StatusFile

        sf = StatusFile(config.status_file, config_path=config.path)
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
        return _run_regression(args, config, manifest, dag, commit_sha)
    elif args.effort in ("converge", "max"):
        return _run_effort(args, config, manifest, dag, commit_sha)

    # Default: run all tests once, no verdict
    executor: SequentialExecutor | AsyncExecutor
    if config.max_parallel == 1:
        executor = SequentialExecutor(
            dag,
            mode=args.mode,
            max_failures=config.max_failures,
        )
    else:
        executor = AsyncExecutor(
            dag,
            mode=args.mode,
            max_failures=config.max_failures,
            max_parallel=config.max_parallel,
        )

    try:
        results = executor.execute()
    except ValueError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        return 1

    _update_status_file(results, config, commit_sha)
    demoted = _print_results(results, args, config, commit_sha, manifest)
    has_failure = any(r.status == "failed" for r in results)
    return 1 if (has_failure or demoted) else 0


def _run_regression(
    args: argparse.Namespace,
    config: TestSetConfig,
    manifest: dict,
    dag: TestDAG,
    commit_sha: str | None = None,
) -> int:
    """Execute with regression option: select tests then run in chosen mode.

    Args:
        args: Parsed CLI arguments.
        config: Loaded test set configuration.
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

    # Configure and run regression selection
    regression_config = RegressionConfig(
        max_test_percentage=config.max_test_percentage,
        max_hops=config.max_hops,
    )

    selection = select_regression_tests(
        changed_files=changed_files,
        graph=graph,
        manifest=manifest,
        config=regression_config,
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
    if config.max_parallel == 1:
        executor = SequentialExecutor(
            filtered_dag,
            mode=args.mode,
            max_failures=config.max_failures,
        )
    else:
        executor = AsyncExecutor(
            filtered_dag,
            mode=args.mode,
            max_failures=config.max_failures,
            max_parallel=config.max_parallel,
        )

    try:
        results = executor.execute()
    except ValueError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        return 1

    _update_status_file(results, config, commit_sha)
    verdict_data = _compute_verdict(args, config, filtered_dag, commit_sha)
    demoted = _print_results(
        results, args, config, commit_sha, filtered_manifest, verdict_data,
    )
    has_failure = any(r.status == "failed" for r in results)
    return 1 if (has_failure or demoted) else 0


def _run_effort(
    args: argparse.Namespace,
    config: TestSetConfig,
    manifest: dict,
    dag: TestDAG,
    commit_sha: str | None = None,
) -> int:
    """Execute with effort converge/max: run tests then SPRT-rerun for classification.

    Args:
        args: Parsed CLI arguments.
        config: Loaded test set configuration.
        manifest: Parsed manifest dict.
        dag: Constructed test DAG.
        commit_sha: Resolved git commit SHA (or None).

    Returns:
        Exit code.
    """
    from orchestrator.execution.effort import EffortRunner
    from orchestrator.lifecycle.status import StatusFile

    if not config.status_file:
        print(
            "Error: --effort converge/max requires status_file in .test_set_config",
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

    sf = StatusFile(config.status_file, config_path=config.path)

    # Phase 1: Execute all tests once
    executor: SequentialExecutor | AsyncExecutor
    if config.max_parallel == 1:
        executor = SequentialExecutor(
            dag,
            mode=args.mode,
            max_failures=config.max_failures,
        )
    else:
        executor = AsyncExecutor(
            dag,
            mode=args.mode,
            max_failures=config.max_failures,
            max_parallel=config.max_parallel,
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
        sf.record_run(r.name, passed, commit=commit_sha)
    sf.save()

    # Phase 2: SPRT rerun loop
    runner = EffortRunner(
        dag=dag,
        status_file=sf,
        commit_sha=commit_sha,
        max_reruns=config.max_reruns,
        effort_mode=args.effort,
        initial_results=initial_results,
    )
    effort_result = runner.run()

    # Phase 3: Verdict
    verdict_data = _compute_verdict(args, config, dag, commit_sha)

    # Phase 4: Print results
    _print_effort_results(
        initial_results, effort_result, args, config, commit_sha, manifest,
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
    config: TestSetConfig,
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
    if args.effort is None or not config.status_file:
        return None

    from orchestrator.lifecycle.e_values import (
        HiFiEvaluator,
        evaluate_test_set,
        verdict_to_dict,
    )
    from orchestrator.lifecycle.status import StatusFile

    sf = StatusFile(config.status_file, config_path=config.path)
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
            max_reruns=config.max_reruns,
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
    results: list, config: TestSetConfig, commit_sha: str | None
) -> None:
    """Update the status file with test results if status_file is configured."""
    if not config.status_file:
        return

    from orchestrator.lifecycle.burnin import process_results
    from orchestrator.lifecycle.status import StatusFile

    sf = StatusFile(config.status_file, config_path=config.path)
    events = process_results(results, sf, commit_sha=commit_sha)
    if events:
        print("\nLifecycle events:")
        for etype, name, old_state, new_state in events:
            print(f"  {name}: {old_state} \u2192 {new_state} ({etype})")


def _print_results(
    results: list, args: argparse.Namespace,
    config: TestSetConfig,
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
        if manifest is not None:
            reporter.set_manifest(manifest)
        reporter.add_results(results)
        if commit_sha:
            reporter.set_commit_hash(commit_sha)
        reporter.set_source_link_base(resolve_source_link_base(commit_sha))

        if verdict_data:
            reporter.set_e_value_verdict(verdict_data)

        # Feed lifecycle data from status file to reporter
        if config.status_file and config.status_file.exists():
            from orchestrator.lifecycle.status import StatusFile

            sf = StatusFile(config.status_file, config_path=config.path)
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
    config: TestSetConfig,
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
          f"(budget: {config.max_reruns} per test)")

    # Generate reports
    if args.output:
        from orchestrator.reporting.source_links import resolve_source_link_base

        reporter = Reporter()
        if manifest is not None:
            reporter.set_manifest(manifest)
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
            "max_reruns_per_test": config.max_reruns,
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

        if config.status_file and config.status_file.exists():
            from orchestrator.lifecycle.status import StatusFile

            sf = StatusFile(config.status_file, config_path=config.path)
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


if __name__ == "__main__":
    sys.exit(main())
