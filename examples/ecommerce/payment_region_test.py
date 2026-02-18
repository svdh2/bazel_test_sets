"""Parameterized payment region test.

Accepts --region and --currency via command-line arguments.
Simulates payment processing for the specified region.
"""

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--currency", required=True)
    args = parser.parse_args()

    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "region", "value": args.region, "unit": ""})
    tst({"type": "measurement", "name": "currency", "value": args.currency, "unit": ""})
    tst({"type": "measurement", "name": "processing_time_ms", "value": 95, "unit": "ms"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": f"payment_{args.region}", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
