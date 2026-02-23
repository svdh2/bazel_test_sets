"""Simulated inventory check test.

This example shows an inventory availability check with query timing
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Stimulation ---
        with t.block("stimulation", description="Check inventory levels") as b:
            with b.step("query_inventory", description="Query SKU availability") as s:
                time.sleep(0.01)
                s.measure("sku_count", 150, "items")
                s.measure("query_time_ms", 8, "ms")

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("inventory_available", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
