"""Reusable helper macro for generating parameterized test sets.

This macro takes a list of named variants, each with its own arguments
and assertion, and generates py_test + test_set_test + test_set targets.

All parameterization is resolved at build time -- the orchestrator and
rules are completely unaware of parameters.
"""

load("@rules_python//python:defs.bzl", "py_test")
load("//rules:test_set_test.bzl", "test_set_test")
load("//rules:test_set.bzl", "test_set")

def parameterized_test_set(
        name,
        test_src,
        variants,
        assertion = "",
        requirement_id = "",
        depends_on = [],
        env = {},
        **kwargs):
    """Generate a test set from a list of parameterized variants.

    Each entry in the variants dict produces a concrete py_test target,
    a test_set_test wrapper, and all are collected into a single test_set.

    Args:
        name: Base name for the generated test set.
        test_src: Source file for the test (shared across all variants).
        variants: Dict of {variant_name: {"args": [...], "assertion": "...", "env": {...}}}.
            Each variant must have at least "assertion". Optional keys:
            - args: List of command-line arguments for py_test.
            - env: Dict of environment variables for py_test.
            - depends_on: List of test_set_test labels this variant depends on.
        assertion: Assertion for the overall test_set.
        requirement_id: Optional requirement ID for the test_set.
        depends_on: List of test_set_test labels that all variants depend on.
        env: Default environment variables applied to all variants.
        **kwargs: Additional attributes passed to test_set.
    """
    tests = []

    for variant_name, config in variants.items():
        test_name = "%s_%s_test" % (name, variant_name)
        wrapped_name = "%s_%s_wrapped" % (name, variant_name)

        # Merge default env with variant-specific env
        variant_env = dict(env)
        variant_env.update(config.get("env", {}))

        # Create the py_test target
        py_test(
            name = test_name,
            srcs = [test_src],
            main = test_src,
            args = config.get("args", []),
            env = variant_env if variant_env else None,
        )

        # Determine depends_on for this variant
        variant_deps = list(depends_on) + config.get("depends_on", [])

        # Extract structured parameters from --key=value args
        variant_params = {}
        for arg in config.get("args", []):
            if arg.startswith("--") and "=" in arg:
                key, _, value = arg[2:].partition("=")
                variant_params[key] = value

        # Create the test_set_test wrapper
        test_set_test(
            name = wrapped_name,
            test = ":" + test_name,
            assertion = config["assertion"],
            depends_on = variant_deps,
            args = config.get("args", []),
            parameters = variant_params,
        )
        tests.append(":" + wrapped_name)

    # Create the test_set
    set_assertion = assertion if assertion else "All %s variants pass" % name
    test_set(
        name = name,
        tests = tests,
        assertion = set_assertion,
        requirement_id = requirement_id,
        **kwargs
    )
