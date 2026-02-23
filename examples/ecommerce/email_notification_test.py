"""Simulated email notification test.

This example shows sending an email notification with configurable
reliability (via TST_EMAIL_RELIABILITY env var) using the tst_sdk
structured logging.
"""

import os
import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("smtp_service", "connect")

        # --- Stimulation ---
        with t.block("stimulation", description="Send email notification") as b:
            with b.step("send_email", description="Send notification email") as s:
                time.sleep(0.01)
                s.measure("send_latency_ms", 85, "ms")

        # --- Verdict ---
        verdict = True
        reliability = float(os.environ.get("TST_EMAIL_RELIABILITY", "1.0"))
        if reliability < 1.0:
            import random
            if random.random() >= reliability:
                verdict = False

        with t.block("verdict") as b:
            b.assert_that("email_delivered", verdict)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
