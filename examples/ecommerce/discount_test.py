"""Simulated discount code test with structured logging."""

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
    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "discount_pct", "value": 15, "unit": "%"})
    tst({"type": "measurement", "name": "original_price", "value": 100.00, "unit": "USD"})
    tst({"type": "measurement", "name": "discounted_price", "value": 85.00, "unit": "USD"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "discount_applied", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
