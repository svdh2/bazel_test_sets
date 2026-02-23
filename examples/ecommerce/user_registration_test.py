"""Simulated user registration test.

This example shows a user registration flow with email validation
using the tst_sdk structured logging.
"""

import sys
import time

from tst_sdk import test_run


def main() -> int:
    with test_run() as t:
        # --- Rigging ---
        with t.block("rigging") as b:
            b.feature("user_service", "initialize")
            b.feature("email_validator", "connect")

        # --- Stimulation ---
        with t.block("stimulation", description="Register new user") as b:
            with b.step("validate_email", description="Validate email format") as s:
                time.sleep(0.005)
                s.assert_that("email_valid", True)

            with b.step("hash_password", description="Hash user password") as s:
                time.sleep(0.005)
                s.measure("hash_latency_ms", 12, "ms")
                s.assert_that("password_hashed", True)

            with b.step("create_user", description="Persist user record") as s:
                time.sleep(0.01)
                s.measure("db_insert_ms", 8, "ms")
                s.assert_that("user_created", True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that("user_registration", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
