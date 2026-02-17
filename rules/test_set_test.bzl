"""test_set_test rule: wraps an existing test target with DAG metadata."""

TestSetTestInfo = provider(
    doc = "Carries metadata for a test_set_test target.",
    fields = {
        "test_label": "Label of the wrapped test target",
        "assertion": "Free-form string describing what this test verifies",
        "requirement_id": "Optional requirement ID for traceability",
        "depends_on": "List of labels this test depends on (DAG edges)",
        "judgement_label": "Optional label of the separate judgement target",
        "executable": "The executable file for the wrapped test",
        "disabled": "Boolean indicating whether this test is disabled",
    },
)

def _test_set_test_impl(ctx):
    # Validate assertion is non-empty
    if not ctx.attr.assertion:
        fail("assertion attribute must be non-empty for test_set_test '{}'".format(ctx.label.name))

    # Get the wrapped test's executable
    wrapped_test = ctx.attr.test
    wrapped_executable = ctx.executable.test

    # Build the runfiles from the wrapped test
    runfiles = ctx.runfiles()
    runfiles = runfiles.merge(wrapped_test[DefaultInfo].default_runfiles)

    # Bake any test args into the runner script so the orchestrator
    # can invoke it without knowing about Bazel's args attribute.
    baked_args = " ".join(['"%s"' % a for a in ctx.attr.args]) if ctx.attr.args else ""

    # Create a runner script that executes the wrapped test
    runner = ctx.actions.declare_file(ctx.label.name + "_runner.sh")
    ctx.actions.write(
        output = runner,
        content = "#!/bin/bash\nexec {exe} {args}\"$@\"\n".format(
            exe = wrapped_executable.short_path,
            args = baked_args + " " if baked_args else "",
        ),
        is_executable = True,
    )

    # Add the wrapped executable and runner to runfiles
    runfiles = runfiles.merge(ctx.runfiles(files = [wrapped_executable, runner]))

    # Collect depends_on labels
    depends_on_labels = [dep.label for dep in ctx.attr.depends_on]

    # Collect judgement label if provided
    judgement_label = None
    if ctx.attr.judgement:
        judgement_label = ctx.attr.judgement.label

    return [
        DefaultInfo(
            executable = runner,
            runfiles = runfiles,
        ),
        TestSetTestInfo(
            test_label = wrapped_test.label,
            assertion = ctx.attr.assertion,
            requirement_id = ctx.attr.requirement_id,
            depends_on = depends_on_labels,
            judgement_label = judgement_label,
            executable = runner,
            disabled = ctx.attr.disabled,
        ),
    ]

test_set_test = rule(
    implementation = _test_set_test_impl,
    attrs = {
        "test": attr.label(
            doc = "The wrapped test target (py_test, cc_test, etc.)",
            mandatory = True,
            executable = True,
            cfg = "target",
        ),
        "depends_on": attr.label_list(
            doc = "List of other test_set_test targets this test depends on",
            default = [],
            providers = [TestSetTestInfo],
        ),
        "assertion": attr.string(
            doc = "Free-form string describing what this test verifies (required)",
            mandatory = True,
        ),
        "requirement_id": attr.string(
            doc = "Optional requirement ID for traceability",
            default = "",
        ),
        "judgement": attr.label(
            doc = "Optional separate judgement target",
            default = None,
            executable = True,
            cfg = "target",
        ),
        "disabled": attr.bool(
            doc = "If True, this test is disabled and will be skipped during execution",
            default = False,
        ),
    },
    test = True,
)
