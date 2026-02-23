"""Simulated user profile update test.

This example shows a basic profile update flow with address verification
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("profile_service", "initialize")

        # --- Stimulation ---
        with t.block("stimulation", description="Update user profile") as b:
            with b.step("update_profile", description="Update name and details") as s:
                time.sleep(0.01)
                s.measure("fields_updated", 2, "fields")
                s.assert_that("profile_updated", True)

            with b.step("verify_address", description="Verify updated address") as s:
                time.sleep(0.005)
                s.measure("verification_latency_ms", 18, "ms")
                s.assert_that("address_verified", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("user_profile_update", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
