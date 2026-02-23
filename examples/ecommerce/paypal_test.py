"""Simulated PayPal payment test.

This example shows a PayPal authorization and charge flow
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("paypal_api", "authenticate")

        # --- Stimulation ---
        with t.block("stimulation", description="Process PayPal payment") as b:
            with b.step("redirect_and_charge", description="PayPal redirect and charge") as s:
                time.sleep(0.01)
                s.measure("redirect_latency_ms", 120, "ms")
                s.measure("charge_amount", 49.99, "USD")
                s.assert_that("paypal_authorized", True, critical=True)

        # --- Checkpoint ---
        with t.block("checkpoint") as b:
            b.assert_that("paypal_authorized", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("paypal_payment", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
