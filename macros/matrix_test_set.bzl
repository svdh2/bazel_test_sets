"""Helper macro for generating test sets from a matrix of parameter combinations.

Each entry in the matrix dict produces a concrete test target with parameters
baked in as command-line arguments. All parameterization is resolved at build
time.
"""

load("@rules_python//python:defs.bzl", "py_test")
load("//rules:test_set_test.bzl", "test_set_test")
load("//rules:test_set.bzl", "test_set")

def matrix_test_set(name, test_src, assertion_template, matrix, deps = [], **kwargs):
    """Generate a test set from a matrix of parameter combinations.

    Each entry in the matrix dict produces a concrete test target.

    Args:
        name: Base name for the generated test set.
        test_src: Source file for the test (shared across all combinations).
        assertion_template: Python format string for assertions, filled with matrix values.
            Use {param_name} placeholders matching the keys in matrix entries.
        matrix: Dict of {variant_name: {param_name: param_value, ...}}.
            Each variant produces one py_test + test_set_test.
        deps: List of py_library dependencies for the generated py_test targets.
        **kwargs: Additional attributes passed to test_set (e.g., requirement_id).
    """
    tests = []
    for variant_name, params in matrix.items():
        args = ["--%s=%s" % (k, v) for k, v in params.items()]

        py_test(
            name = "%s_%s_test" % (name, variant_name),
            srcs = [test_src],
            main = test_src,
            args = args,
            deps = deps,
        )

        test_set_test(
            name = "%s_%s_wrapped" % (name, variant_name),
            test = ":%s_%s_test" % (name, variant_name),
            assertion = assertion_template.format(**params),
            args = args,
            parameters = {k: str(v) for k, v in params.items()},
        )
        tests.append(":%s_%s_wrapped" % (name, variant_name))

    test_set(
        name = name,
        tests = tests,
        **kwargs
    )
