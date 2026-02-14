"""Example parameter mapping macro: memory limit tests by deployment tier.

Demonstrates single-parameter parameterization where a mapping function
derives concrete test values from high-level parameters.
"""

load("@rules_python//python:defs.bzl", "py_test")
load("//rules:test_set_test.bzl", "test_set_test")
load("//rules:test_set.bzl", "test_set")

def memory_test_set(name, tier, max_ram_gb):
    """Parameter mapping macro: generates concrete test targets for memory limits.

    The mapping function derives per-service memory limits from the total
    RAM allocation and service fraction configuration.

    Args:
        name: Base name for generated targets.
        tier: Deployment tier (e.g., "production", "staging").
        max_ram_gb: Maximum total RAM allocation in GB.
    """
    services = {
        "api_gateway": {"fraction": 0.3},
        "worker": {"fraction": 0.5},
        "cache": {"fraction": 0.2},
    }

    tests = []
    for svc, config in services.items():
        limit_gb = max_ram_gb * config["fraction"]

        # Generate concrete py_test with parameters baked in
        py_test(
            name = "%s_%s_test" % (name, svc),
            srcs = ["memory_test.py"],
            main = "memory_test.py",
            args = ["--service=%s" % svc, "--limit-gb=%s" % limit_gb],
            env = {"DEPLOYMENT_TIER": tier},
        )

        # Wrap with test_set_test
        test_set_test(
            name = "%s_%s_wrapped" % (name, svc),
            test = ":%s_%s_test" % (name, svc),
            assertion = "%s memory stays under %sGB on %s" % (svc, limit_gb, tier),
            args = ["--service=%s" % svc, "--limit-gb=%s" % limit_gb],
        )
        tests.append(":%s_%s_wrapped" % (name, svc))

    # Generate the test_set
    test_set(
        name = name,
        assertion = "All service memory within limits for %s (max %sGB)" % (tier, max_ram_gb),
        tests = tests,
    )
