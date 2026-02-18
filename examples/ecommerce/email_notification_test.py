"""Simulated email notification test with structured logging."""

import json
import os
import sys
import time


def tst(event: dict) -> None:
    """Emit a structured test log event with source location."""
    frame = sys._getframe(1)
    rel = os.path.relpath(frame.f_code.co_filename)
    event = {**event, "_file": rel, "_line": frame.f_lineno}
    print(f"[TST] {json.dumps(event)}")


def main() -> int:
    tst({"type": "block_start", "block": "rigging"})
    tst({"type": "feature", "name": "smtp_service", "action": "connect"})
    tst({"type": "block_end", "block": "rigging"})

    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "send_latency_ms", "value": 85, "unit": "ms"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "email_delivered", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    reliability = float(os.environ.get("TST_EMAIL_RELIABILITY", "1.0"))
    if reliability < 1.0:
        import random
        if random.random() >= reliability:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
