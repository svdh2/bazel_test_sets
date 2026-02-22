"""Simulated order placement test demonstrating steps within blocks.

This example shows how to use step_start/step_end events to subdivide
a stimulation block into named sub-operations with nested steps,
measurements, and assertions.
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
            b.feature("inventory_service", "connect")

        # --- Stimulation with steps ---
        with t.block("stimulation", description="Place order with steps") as b:

            # Step 1: Create order (top-level)
            with b.step("create_order", description="Create a new order") as s:
                time.sleep(0.01)
                s.measure("order_total", 129.97, "USD")
                s.measure("items_count", 3, "items")

                # Step 1.1: Validate inventory (critical — no point continuing without stock)
                with s.step("validate_inventory", description="Check inventory for all items") as s2:
                    time.sleep(0.005)
                    s2.measure("stock_check_ms", 15, "ms")
                    s2.assert_that("all_items_in_stock", True, critical=True)

                # Step 1.2: Process payment (critical — can't ship without payment)
                with s.step("process_payment", description="Charge payment method") as s2:
                    time.sleep(0.01)
                    s2.measure("charge_amount", 129.97, "USD")
                    s2.measure("payment_latency_ms", 250, "ms")
                    s2.assert_that("payment_authorized", True, critical=True)

                # Step 1.3: Send confirmation (soft — order still valid without email)
                with s.step("send_confirmation", description="Send order confirmation email") as s2:
                    time.sleep(0.005)
                    s2.measure("email_latency_ms", 50, "ms")
                    s2.assert_that("confirmation_sent", True)

        # --- Checkpoint ---
        with t.block("checkpoint") as b:
            b.assert_that("order_created", True)
            b.assert_that("payment_processed", True)
            b.assert_that("inventory_decremented", True)
            b.assert_that("confirmation_email_sent", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("order_steps_flow", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
