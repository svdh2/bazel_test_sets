"""Simulated credit card payment test with structured logging."""

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
    time.sleep(0.01)
    tst({"type": "feature", "name": "payment_gateway", "action": "connect"})
    tst({"type": "block_end", "block": "rigging"})

    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "auth_latency_ms", "value": 45, "unit": "ms"})
    tst({"type": "measurement", "name": "charge_amount", "value": 99.99, "unit": "USD"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "checkpoint"})
    tst({"type": "result", "name": "charge_authorized", "passed": True})
    tst({"type": "result", "name": "receipt_generated", "passed": True})
    tst({"type": "block_end", "block": "checkpoint"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "credit_card_payment", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
