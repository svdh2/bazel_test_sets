"""Simulated discount code test.

This example shows applying a discount code and verifying the
discounted price using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Stimulation ---
        with t.block("stimulation", description="Apply discount code") as b:
            with b.step("apply_discount", description="Apply 15% discount to order") as s:
                time.sleep(0.01)
                s.measure("discount_pct", 15, "%")
                s.measure("original_price", 100.00, "USD")
                s.measure("discounted_price", 85.00, "USD")

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("discount_applied", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
