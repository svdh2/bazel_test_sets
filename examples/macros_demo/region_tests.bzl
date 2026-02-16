"""Example matrix testing macro: payment processing across regions.

Demonstrates the matrix_test_set pattern where each entry in the matrix
produces a concrete test target with parameters baked in as arguments.
"""

load("@test_sets_bazel_rules//macros:matrix_test_set.bzl", "matrix_test_set")

def payment_region_test_set(name, **kwargs):
    """Generate payment processing test set across regions and currencies.

    Uses matrix_test_set to generate one test per region/currency combination.

    Args:
        name: Base name for generated targets.
        **kwargs: Additional attributes passed to the test_set.
    """
    matrix_test_set(
        name = name,
        test_src = "payment_region_test.py",
        assertion_template = "Payments process correctly in {region} with {currency}",
        assertion = "Payment processing works across all regions and currencies",
        requirement_id = "REQ-PAY-REGION-001",
        matrix = {
            "us_usd": {"region": "us", "currency": "USD"},
            "eu_eur": {"region": "eu", "currency": "EUR"},
            "uk_gbp": {"region": "uk", "currency": "GBP"},
            "jp_jpy": {"region": "jp", "currency": "JPY"},
        },
        **kwargs
    )
