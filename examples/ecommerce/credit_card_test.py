"""Simulated credit card payment test.

This example shows a credit card authorization and charge flow
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("payment_gateway", "connect")

        # --- Stimulation ---
        with t.block("stimulation", description="Process credit card payment") as b:
            with b.step("authorize_charge", description="Authorize credit card") as s:
                time.sleep(0.01)
                s.measure("auth_latency_ms", 45, "ms")
                s.measure("charge_amount", 99.99, "USD")
                s.assert_that("charge_authorized", True, critical=True)

            with b.step("generate_receipt", description="Generate payment receipt") as s:
                time.sleep(0.005)
                s.assert_that("receipt_generated", True)

        # --- Checkpoint ---
        with t.block("checkpoint") as b:
            b.assert_that("charge_authorized", True)
            b.assert_that("receipt_generated", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("credit_card_payment", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
