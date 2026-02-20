"""Simulated order placement test demonstrating steps within blocks.

This example shows how to use step_start/step_end events to subdivide
a stimulation block into named sub-operations with nested steps,
measurements, and assertions.
"""

import json
import sys
import time


def tst(event: dict) -> None:
    """Emit a structured test log event with source location."""
    import os
    frame = sys._getframe(1)
    rel = os.path.relpath(frame.f_code.co_filename)
    event = {**event, "_file": rel, "_line": frame.f_lineno}
    print(f"[TST] {json.dumps(event)}")


def main() -> int:
    # --- Rigging ---
    tst({"type": "block_start", "block": "rigging"})
    tst({"type": "feature", "name": "order_service", "action": "initialize"})
    tst({"type": "feature", "name": "payment_gateway", "action": "connect"})
    tst({"type": "feature", "name": "inventory_service", "action": "connect"})
    tst({"type": "block_end", "block": "rigging"})

    # --- Stimulation with steps ---
    tst({
        "type": "block_start",
        "block": "stimulation",
        "description": "Place order with steps",
    })

    # Step 1: Create order (top-level)
    tst({
        "type": "step_start",
        "step": "create_order",
        "description": "Create a new order",
    })
    time.sleep(0.01)
    tst({"type": "measurement", "name": "order_total", "value": 129.97, "unit": "USD"})
    tst({"type": "measurement", "name": "items_count", "value": 3, "unit": "items"})

    # Step 1.1: Validate inventory (nested)
    tst({
        "type": "step_start",
        "step": "validate_inventory",
        "description": "Check inventory for all items",
    })
    time.sleep(0.005)
    tst({"type": "measurement", "name": "stock_check_ms", "value": 15, "unit": "ms"})
    tst({"type": "result", "name": "all_items_in_stock", "passed": True})
    tst({"type": "step_end", "step": "validate_inventory"})

    # Step 1.2: Process payment (nested)
    tst({
        "type": "step_start",
        "step": "process_payment",
        "description": "Charge payment method",
    })
    time.sleep(0.01)
    tst({"type": "measurement", "name": "charge_amount", "value": 129.97, "unit": "USD"})
    tst({"type": "measurement", "name": "payment_latency_ms", "value": 250, "unit": "ms"})
    tst({"type": "result", "name": "payment_authorized", "passed": True})
    tst({"type": "step_end", "step": "process_payment"})

    # Step 1.3: Send confirmation (nested)
    tst({
        "type": "step_start",
        "step": "send_confirmation",
        "description": "Send order confirmation email",
    })
    time.sleep(0.005)
    tst({"type": "measurement", "name": "email_latency_ms", "value": 50, "unit": "ms"})
    tst({"type": "result", "name": "confirmation_sent", "passed": True})
    tst({"type": "step_end", "step": "send_confirmation"})

    tst({"type": "step_end", "step": "create_order"})
    tst({"type": "block_end", "block": "stimulation"})

    # --- Checkpoint ---
    tst({"type": "block_start", "block": "checkpoint"})
    tst({"type": "result", "name": "order_created", "passed": True})
    tst({"type": "result", "name": "payment_processed", "passed": True})
    tst({"type": "result", "name": "inventory_decremented", "passed": True})
    tst({"type": "result", "name": "confirmation_email_sent", "passed": True})
    tst({"type": "block_end", "block": "checkpoint"})

    # --- Verdict ---
    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "order_steps_flow", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
