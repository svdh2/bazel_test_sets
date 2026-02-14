"""Simulated inventory check test with structured logging."""

import json
import sys
import time


def tst(event: dict) -> None:
    """Emit a structured test log event."""
    print(f"[TST] {json.dumps(event)}")


def main() -> int:
    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "sku_count", "value": 150, "unit": "items"})
    tst({"type": "measurement", "name": "query_time_ms", "value": 8, "unit": "ms"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "inventory_available", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
