"""Simulated user login test with structured logging."""

import json
import sys
import time


def tst(event: dict) -> None:
    """Emit a structured test log event."""
    print(f"[TST] {json.dumps(event)}")


def main() -> int:
    tst({"type": "block_start", "block": "rigging"})
    tst({"type": "feature", "name": "auth_service", "action": "initialize"})
    tst({"type": "block_end", "block": "rigging"})

    tst({"type": "block_start", "block": "stimulation"})
    time.sleep(0.01)
    tst({"type": "measurement", "name": "login_latency_ms", "value": 32, "unit": "ms"})
    tst({"type": "block_end", "block": "stimulation"})

    tst({"type": "block_start", "block": "verdict"})
    tst({"type": "result", "name": "user_authenticated", "passed": True})
    tst({"type": "block_end", "block": "verdict"})

    return 0


if __name__ == "__main__":
    sys.exit(main())
