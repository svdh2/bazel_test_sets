"""Simulated shipping calculation test.

This example shows calculating shipping costs for an order
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("shipping_service", "initialize")

        # --- Stimulation ---
        with t.block("stimulation", description="Calculate shipping for order") as b:
            with b.step("calculate_shipping", description="Compute cost for order #12345") as s:
                time.sleep(0.01)
                s.measure("weight", 2.5, "kg")
                s.measure("shipping_cost", 7.99, "USD")
                s.measure("estimated_days", 4, "days")
                s.assert_that("shipping_calculated", True)

        # --- Checkpoint ---
        with t.block("checkpoint") as b:
            b.assert_that("destination_valid", True)
            b.assert_that("cost_within_range", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("shipping_calculation", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
