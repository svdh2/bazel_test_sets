"""Simulated PayPal payment test with structured logging."""

import json
import sys
import time


def tst(event: dict) -> None:
    """Emit a structured test log event."""
    print(f"[TST] {json.dumps(event)}")


def main() -> int:
    tst({"type": "block_start", "block": "rigging"})
    time.sleep(0.01)
    tst({"type": "feature", "name": "paypal_api", "action": "authenticate"})
    tst({"type": "block_end", "block": "rigging"})

    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "redirect_latency_ms", "value": 120, "unit": "ms"})
    tst({"type": "measurement", "name": "charge_amount", "value": 49.99, "unit": "USD"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "checkpoint"})
    tst({"type": "result", "name": "paypal_authorized", "passed": True})
    tst({"type": "block_end", "block": "checkpoint"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "paypal_payment", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
