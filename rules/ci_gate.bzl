"""ci_gate rule: bundles a test_set with execution policy and lifecycle parameters.

A ci_gate target generates a runner script that invokes the orchestrator with
baked-in flags derived from the rule attributes. Each CI stage is a ci_gate
target: `bazel run //ci:pr_gate_tests` runs the referenced test set with the
configured execution parameters. Additional CLI args can be passed via "$@"
for overrides.
"""

load("//rules:test_set.bzl", "TestSetInfo")

def _validate_float_string(name, value):
    """Validate that a string represents a valid float in [0.0, 1.0]."""

    # Starlark has no native float parsing, so we validate the format manually.
    # Accept strings like "0.10", "0.99", "0.999", "1.0", "0.0".
    if not value:
        fail("Attribute '{}' must be non-empty".format(name))

    # Check for valid float-like patterns: digits, at most one dot
    has_dot = False
    for c in value.elems():
        if c == ".":
            if has_dot:
                fail("Attribute '{}' has invalid float format: '{}'".format(name, value))
            has_dot = True
        elif c < "0" or c > "9":
            fail("Attribute '{}' has invalid float format: '{}'".format(name, value))

    if not has_dot:
        fail("Attribute '{}' must contain a decimal point: '{}'".format(name, value))

def _ci_gate_rule_test_impl(ctx):
    # Validate float-string attributes
    _validate_float_string("max_test_percentage", ctx.attr.max_test_percentage)
    _validate_float_string("min_reliability", ctx.attr.min_reliability)
    _validate_float_string("statistical_significance", ctx.attr.statistical_significance)

    # Get the test_set target's manifest and runfiles
    test_set_info = ctx.attr.test_set[TestSetInfo]
    test_set_default = ctx.attr.test_set[DefaultInfo]

    # Get the manifest file from the TestSetInfo provider
    manifest_file = test_set_info.manifest

    # Collect the orchestrator executable and its runfiles
    orchestrator_exe = ctx.executable._orchestrator

    # Build runfiles from test_set and orchestrator
    runfiles = ctx.runfiles(files = [manifest_file, orchestrator_exe])
    runfiles = runfiles.merge(test_set_default.default_runfiles)
    runfiles = runfiles.merge(ctx.attr._orchestrator[DefaultInfo].default_runfiles)

    # Build the flag list -- only include non-default/non-None attributes
    flags = []

    # Mode (always include since it's a fundamental parameter)
    flags.append('  --mode "{}"'.format(ctx.attr.mode))

    # Effort (only if set)
    if ctx.attr.effort:
        flags.append('  --effort "{}"'.format(ctx.attr.effort))

    # Integer flags (only if non-default)
    if ctx.attr.max_reruns != 100:
        flags.append("  --max-reruns {}".format(ctx.attr.max_reruns))
    if ctx.attr.max_failures > 0:
        flags.append("  --max-failures {}".format(ctx.attr.max_failures))
    if ctx.attr.max_parallel > 0:
        flags.append("  --max-parallel {}".format(ctx.attr.max_parallel))
    if ctx.attr.max_hops != 2:
        flags.append("  --max-hops {}".format(ctx.attr.max_hops))
    if ctx.attr.flaky_deadline_days != 14:
        flags.append("  --flaky-deadline-days {}".format(ctx.attr.flaky_deadline_days))

    # Path flags (resolved relative to BUILD_WORKSPACE_DIRECTORY)
    if ctx.attr.status_file:
        flags.append('  --status-file "${{BUILD_WORKSPACE_DIRECTORY:-.}}/{}"'.format(ctx.attr.status_file))
    if ctx.attr.diff_base:
        flags.append('  --diff-base "{}"'.format(ctx.attr.diff_base))
    if ctx.attr.co_occurrence_graph:
        flags.append('  --co-occurrence-graph "${{BUILD_WORKSPACE_DIRECTORY:-.}}/{}"'.format(ctx.attr.co_occurrence_graph))

    # Float-string flags (only if non-default)
    if ctx.attr.max_test_percentage != "0.10":
        flags.append("  --max-test-percentage {}".format(ctx.attr.max_test_percentage))
    if ctx.attr.min_reliability != "0.99":
        flags.append("  --min-reliability {}".format(ctx.attr.min_reliability))
    if ctx.attr.statistical_significance != "0.95":
        flags.append("  --statistical-significance {}".format(ctx.attr.statistical_significance))

    # Boolean flag (only if non-default, default is True)
    if not ctx.attr.skip_unchanged:
        flags.append("  --no-skip-unchanged")

    # Join flags with line continuations
    flags_str = " \\\n".join(flags) if flags else ""
    if flags_str:
        flags_str = " \\\n" + flags_str

    # Create runner script that invokes the orchestrator with baked-in flags
    runner = ctx.actions.declare_file(ctx.label.name + "_runner.sh")
    ctx.actions.write(
        output = runner,
        content = """\
#!/bin/bash
# ci_gate runner for: {name}
# test_set: {test_set_name}
if [[ -n "${{RUNFILES_DIR:-}}" ]]; then
  R="$RUNFILES_DIR"
elif [[ -d "$0.runfiles" ]]; then
  R="$0.runfiles"
else
  echo "Cannot find runfiles" >&2; exit 1
fi
OUT_DIR="${{BUILD_WORKSPACE_DIRECTORY:-.}}/target/reports"
mkdir -p "$OUT_DIR"
exec "$R/{workspace}/{orchestrator}" \\
  --manifest "$R/{workspace}/{manifest}" \\
  --output "$OUT_DIR/{name}.json" \\
  --discover-workspace-tests \\
  --ci-gate-name "{name}"{flags} \\
  "$@"
""".format(
            name = ctx.label.name,
            test_set_name = test_set_info.name,
            workspace = ctx.workspace_name,
            orchestrator = orchestrator_exe.short_path,
            manifest = manifest_file.short_path,
            flags = flags_str,
        ),
        is_executable = True,
    )

    return [
        DefaultInfo(
            executable = runner,
            runfiles = runfiles,
        ),
    ]

_ci_gate_rule_test = rule(
    implementation = _ci_gate_rule_test_impl,
    attrs = {
        "test_set": attr.label(
            doc = "The test_set target to run",
            mandatory = True,
            providers = [TestSetInfo],
        ),
        "mode": attr.string(
            doc = "Execution mode: 'diagnostic' (leaves-first, dependency gating) or 'detection' (roots-first, no gating)",
            default = "diagnostic",
            values = ["diagnostic", "detection"],
        ),
        "effort": attr.string(
            doc = "Effort mode: 'regression', 'converge', 'max', or empty for none",
            default = "",
        ),
        "max_reruns": attr.int(
            doc = "Maximum SPRT reruns per test (default: 100)",
            default = 100,
        ),
        "max_failures": attr.int(
            doc = "Stop after N failures in detection mode (0 = no limit)",
            default = 0,
        ),
        "max_parallel": attr.int(
            doc = "Maximum parallel test executions (0 = no limit)",
            default = 0,
        ),
        "status_file": attr.string(
            doc = "Path to the status file relative to workspace root (enables lifecycle features)",
            default = "",
        ),
        "diff_base": attr.string(
            doc = "Git ref for regression diff (e.g., 'main', 'HEAD~3')",
            default = "",
        ),
        "co_occurrence_graph": attr.string(
            doc = "Path to co-occurrence graph file relative to workspace root",
            default = "",
        ),
        "max_test_percentage": attr.string(
            doc = "Regression: max fraction of tests to select (default: '0.10')",
            default = "0.10",
        ),
        "max_hops": attr.int(
            doc = "Regression: max BFS hops in co-occurrence graph (default: 2)",
            default = 2,
        ),
        "skip_unchanged": attr.bool(
            doc = "Skip hash-unchanged tests with conclusive SPRT (default: True)",
            default = True,
        ),
        "min_reliability": attr.string(
            doc = "SPRT: minimum reliability threshold (default: '0.99')",
            default = "0.99",
        ),
        "statistical_significance": attr.string(
            doc = "SPRT: significance level for decisions (default: '0.95')",
            default = "0.95",
        ),
        "flaky_deadline_days": attr.int(
            doc = "Days before flaky tests auto-disable (default: 14)",
            default = 14,
        ),
        "_orchestrator": attr.label(
            default = Label("//orchestrator:main"),
            executable = True,
            cfg = "target",
        ),
    },
    test = True,
)

def ci_gate(name, test_set, mode = "diagnostic", effort = None, max_reruns = 100,
            max_failures = None, max_parallel = None, status_file = None,
            diff_base = None, co_occurrence_graph = None,
            max_test_percentage = "0.10", max_hops = 2, skip_unchanged = True,
            min_reliability = "0.99", statistical_significance = "0.95",
            flaky_deadline_days = 14, **kwargs):
    """Macro that creates a ci_gate target.

    Bundles a test_set reference with execution policy and statistical/lifecycle
    parameters into a single runnable Bazel target. The generated runner script
    invokes the orchestrator with baked-in flags.

    Args:
        name: Name for the ci_gate target (e.g., "pr_gate_tests").
        test_set: Label of the test_set target to run.
        mode: Execution mode: "diagnostic" or "detection".
        effort: Effort mode: "regression", "converge", "max", or None for none.
        max_reruns: Maximum SPRT reruns per test (default: 100).
        max_failures: Stop after N failures in detection mode (None = no limit).
        max_parallel: Maximum parallel test executions (None = no limit).
        status_file: Path to the status file relative to workspace root.
        diff_base: Git ref for regression diff (e.g., "main").
        co_occurrence_graph: Path to co-occurrence graph file.
        max_test_percentage: Regression: max fraction of tests to select (default: "0.10").
        max_hops: Regression: max BFS hops in co-occurrence graph (default: 2).
        skip_unchanged: Skip hash-unchanged tests with conclusive SPRT (default: True).
        min_reliability: SPRT: minimum reliability threshold (default: "0.99").
        statistical_significance: SPRT: significance level (default: "0.95").
        flaky_deadline_days: Days before flaky tests auto-disable (default: 14).
        **kwargs: Additional arguments passed to the underlying rule.
    """

    # Bazel requires test rule names to end with _test
    if name.endswith("_test") or name.endswith("_tests"):
        rule_name = name
    else:
        rule_name = name + "_test"

    _ci_gate_rule_test(
        name = rule_name,
        test_set = test_set,
        mode = mode,
        effort = effort or "",
        max_reruns = max_reruns,
        max_failures = max_failures or 0,
        max_parallel = max_parallel or 0,
        status_file = status_file or "",
        diff_base = diff_base or "",
        co_occurrence_graph = co_occurrence_graph or "",
        max_test_percentage = max_test_percentage,
        max_hops = max_hops,
        skip_unchanged = skip_unchanged,
        min_reliability = min_reliability,
        statistical_significance = statistical_significance,
        flaky_deadline_days = flaky_deadline_days,
        **kwargs
    )

    # Create an alias if the name doesn't match the rule name
    if rule_name != name:
        alias_kwargs = {"name": name, "actual": ":" + rule_name}
        if "visibility" in kwargs:
            alias_kwargs["visibility"] = kwargs["visibility"]
        native.alias(**alias_kwargs)
