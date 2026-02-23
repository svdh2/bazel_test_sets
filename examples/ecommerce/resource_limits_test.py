"""Parameterized resource limits test.

Accepts DEPLOY_TIER and MAX_CONNECTIONS via environment variables.
Simulates resource allocation verification for deployment tiers
using the tst_sdk structured logging.
"""

import os
import sys
import time

from tst_sdk import test_run


def main() -> int:
    tier = os.environ.get("DEPLOY_TIER", "development")
    max_conn = int(os.environ.get("MAX_CONNECTIONS", "10"))

    with test_run() as t:
        # --- Stimulation ---
        with t.block("stimulation", description="Verify resource limits") as b:
            with b.step("check_limits", description=f"Check limits for {tier} tier") as s:
                time.sleep(0.01)
                s.measure("tier", tier, "")
                s.measure("max_connections", max_conn, "conn")

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that(f"resource_limits_{tier}", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
