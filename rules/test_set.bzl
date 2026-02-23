"""test_set rule: declares a test set with tests and subsets, generates JSON manifest."""

load("//rules:test_set_test.bzl", "TestSetTestInfo")

TestSetInfo = provider(
    doc = "Carries metadata for a test_set target, including collected tests.",
    fields = {
        "name": "Name of the test set",
        "assertion": "Free-form string describing what this test set verifies",
        "requirement_id": "Optional requirement ID for traceability",
        "test_labels": "List of direct test_set_test labels in this set",
        "subset_labels": "List of direct subset test_set labels",
        "all_tests": "Depset of all TestSetTestInfo across tests and subsets",
        "tree": "Recursive tree dict for hierarchical reporting",
    },
)

def _collect_all_tests(tests, subsets):
    """Collect all TestSetTestInfo from direct tests and nested subsets."""
    all_test_infos = []

    # Collect from direct tests
    for t in tests:
        all_test_infos.append(t[TestSetTestInfo])

    # Collect transitive from subsets
    transitive = []
    for s in subsets:
        transitive.append(s[TestSetInfo].all_tests)

    return depset(all_test_infos, transitive = transitive)

def _test_info_to_dict(info):
    """Convert a TestSetTestInfo to a manifest dict entry."""
    entry = {
        "assertion": info.assertion,
        "executable": info.executable.short_path,
        "depends_on": [str(d) for d in info.depends_on],
    }
    if info.requirement_id:
        entry["requirement_id"] = info.requirement_id
    if info.judgement_label:
        entry["judgement_executable"] = str(info.judgement_label)
    if info.disabled:
        entry["disabled"] = True
    if info.parameters:
        entry["parameters"] = dict(info.parameters)
    return entry

def _test_set_rule_test_impl(ctx):
    # Validate assertion is non-empty
    if not ctx.attr.assertion:
        fail("assertion attribute must be non-empty for test_set '{}'".format(ctx.label.name))

    # Collect all tests transitively
    all_tests = _collect_all_tests(ctx.attr.tests, ctx.attr.subsets)

    # Build hierarchical tree for reporting (uses raw test labels)
    tree = {
        "name": ctx.label.name,
        "assertion": ctx.attr.assertion,
        "tests": [str(t[TestSetTestInfo].test_label) for t in ctx.attr.tests],
        "subsets": [s[TestSetInfo].tree for s in ctx.attr.subsets],
    }
    if ctx.attr.requirement_id:
        tree["requirement_id"] = ctx.attr.requirement_id

    manifest = {
        "test_set": tree,
        "test_set_tests": {},
    }

    # Populate test_set_tests from all collected tests
    for info in all_tests.to_list():
        key = str(info.test_label)
        manifest["test_set_tests"][key] = _test_info_to_dict(info)

    # Write manifest JSON
    manifest_file = ctx.actions.declare_file(ctx.label.name + "_manifest.json")
    ctx.actions.write(
        output = manifest_file,
        content = json.encode_indent(manifest, indent = "  "),
    )

    # Collect all runfiles from all tests, subsets, and the orchestrator
    orchestrator_exe = ctx.executable._orchestrator
    runfiles = ctx.runfiles(files = [manifest_file, orchestrator_exe])
    for t in ctx.attr.tests:
        runfiles = runfiles.merge(t[DefaultInfo].default_runfiles)
    for s in ctx.attr.subsets:
        runfiles = runfiles.merge(s[DefaultInfo].default_runfiles)
    runfiles = runfiles.merge(ctx.attr._orchestrator[DefaultInfo].default_runfiles)

    # Create runner script that invokes the orchestrator
    runner = ctx.actions.declare_file(ctx.label.name + "_runner.sh")
    ctx.actions.write(
        output = runner,
        content = """\
#!/bin/bash
# Runner for test_set: {name}
if [[ -n "${{RUNFILES_DIR:-}}" ]]; then
  R="$RUNFILES_DIR"
elif [[ -d "$0.runfiles" ]]; then
  R="$0.runfiles"
else
  echo "Cannot find runfiles" >&2; exit 1
fi
OUT_DIR="${{BUILD_WORKSPACE_DIRECTORY:-.}}/target/reports"
CONFIG="${{BUILD_WORKSPACE_DIRECTORY:-.}}/.test_set_config"
mkdir -p "$OUT_DIR"
exec "$R/{workspace}/{orchestrator}" \\
  --manifest "$R/{workspace}/{manifest}" \\
  --output "$OUT_DIR/{name}.json" \\
  --config-file "$CONFIG" \\
  --discover-workspace-tests \\
  "$@"
""".format(
            name = ctx.label.name,
            workspace = ctx.workspace_name,
            orchestrator = orchestrator_exe.short_path,
            manifest = manifest_file.short_path,
        ),
        is_executable = True,
    )

    return [
        DefaultInfo(
            executable = runner,
            runfiles = runfiles,
        ),
        TestSetInfo(
            name = ctx.label.name,
            assertion = ctx.attr.assertion,
            requirement_id = ctx.attr.requirement_id,
            test_labels = [t.label for t in ctx.attr.tests],
            subset_labels = [s.label for s in ctx.attr.subsets],
            all_tests = all_tests,
            tree = tree,
        ),
    ]

_test_set_rule_test = rule(
    implementation = _test_set_rule_test_impl,
    attrs = {
        "tests": attr.label_list(
            doc = "List of test_set_test targets in this set",
            default = [],
            providers = [TestSetTestInfo],
        ),
        "subsets": attr.label_list(
            doc = "List of child test_set targets",
            default = [],
            providers = [TestSetInfo],
        ),
        "assertion": attr.string(
            doc = "Free-form string describing what this test set verifies (required)",
            mandatory = True,
        ),
        "requirement_id": attr.string(
            doc = "Optional requirement ID for traceability",
            default = "",
        ),
        "_orchestrator": attr.label(
            default = Label("//orchestrator:main"),
            executable = True,
            cfg = "target",
        ),
    },
    test = True,
)

def test_set(name, tests = [], subsets = [], assertion = "", requirement_id = "", **kwargs):
    """Macro that creates a test_set target.

    The underlying rule requires a name ending in '_test', so this macro
    handles the naming automatically. If the name already ends with '_test',
    it uses it directly; otherwise it appends '_test' for the rule target
    and creates an alias with the original name.

    Args:
        name: Name for the test set target.
        tests: List of test_set_test targets.
        subsets: List of child test_set targets.
        assertion: Free-form string describing what this test set verifies.
        requirement_id: Optional requirement ID for traceability.
        **kwargs: Additional arguments passed to the underlying rule.
    """
    # Bazel requires test rule names to end with _test
    if name.endswith("_test") or name.endswith("_tests"):
        rule_name = name
    else:
        rule_name = name + "_test"

    _test_set_rule_test(
        name = rule_name,
        tests = tests,
        subsets = subsets,
        assertion = assertion,
        requirement_id = requirement_id,
        **kwargs
    )

    # Create an alias if the name doesn't match the rule name
    if rule_name != name:
        alias_kwargs = {"name": name, "actual": ":" + rule_name}
        if "visibility" in kwargs:
            alias_kwargs["visibility"] = kwargs["visibility"]
        native.alias(**alias_kwargs)
