"""Simulated shopping cart test.

This example shows adding items to a cart and validating totals
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("cart_service", "initialize")

        # --- Stimulation ---
        with t.block("stimulation", description="Add items to cart") as b:
            with b.step("add_first_item", description="Add SKU-001 to cart") as s:
                time.sleep(0.01)
                s.measure("item_price", 29.99, "USD")
                s.assert_that("item_sku001_added", True)

            with b.step("add_second_item", description="Add SKU-002 to cart") as s:
                time.sleep(0.01)
                s.measure("item_price", 24.99, "USD")
                s.assert_that("item_sku002_added", True)

        # --- Checkpoint ---
        with t.block("checkpoint") as b:
            b.measure("cart_total", 54.98, "USD")
            b.assert_that("cart_total_correct", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("cart_validation", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
