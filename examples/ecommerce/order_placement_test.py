"""Simulated order placement test with structured logging."""

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
    tst({"type": "block_start", "block": "rigging"})
    tst({"type": "feature", "name": "order_service", "action": "initialize"})
    tst({"type": "feature", "name": "payment_gateway", "action": "connect"})
    tst({"type": "block_end", "block": "rigging"})

    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.02)
    tst({"type": "measurement", "name": "order_total", "value": 129.97, "unit": "USD"})
    tst({"type": "measurement", "name": "items_count", "value": 3, "unit": "items"})
    tst({"type": "measurement", "name": "order_placement_ms", "value": 250, "unit": "ms"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "checkpoint"})
    tst({"type": "result", "name": "order_created", "passed": True})
    tst({"type": "result", "name": "payment_processed", "passed": True})
    tst({"type": "result", "name": "inventory_decremented", "passed": True})
    tst({"type": "block_end", "block": "checkpoint"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "order_placement", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
