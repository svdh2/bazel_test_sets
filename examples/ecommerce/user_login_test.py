"""Simulated user login test.

This example shows a user authentication flow with latency measurement
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("auth_service", "initialize")

        # --- Stimulation ---
        with t.block("stimulation", description="Authenticate user") as b:
            with b.step("login", description="Perform user login") as s:
                time.sleep(0.01)
                s.measure("login_latency_ms", 32, "ms")
                s.assert_that("credentials_valid", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("user_authenticated", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
