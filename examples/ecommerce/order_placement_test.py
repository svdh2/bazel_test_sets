"""Simulated order placement test.

This example shows an order placement flow with payment processing
and inventory management using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("order_service", "initialize")
            b.feature("payment_gateway", "connect")

        # --- Stimulation ---
        with t.block("stimulation", description="Place order") as b:
            with b.step("place_order", description="Submit order for processing") as s:
                time.sleep(0.02)
                s.measure("order_total", 129.97, "USD")
                s.measure("items_count", 3, "items")
                s.measure("order_placement_ms", 250, "ms")

        # --- Checkpoint ---
        with t.block("checkpoint") as b:
            b.assert_that("order_created", True)
            b.assert_that("payment_processed", True)
            b.assert_that("inventory_decremented", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("order_placement", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
