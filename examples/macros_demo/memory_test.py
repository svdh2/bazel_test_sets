"""Mock memory limit test for parameterization demo.

This test simulates checking memory limits for a service.
Arguments are baked in by the parameterized_test_set macro.
Uses the tst_sdk structured logging.
"""

import argparse
import sys

from tst_sdk import test_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--limit-gb", type=float, required=True)
    args = parser.parse_args()

    with test_run() as t:
        # --- Stimulation ---
        with t.block("stimulation", description=f"Check memory for {args.service}") as b:
            with b.step("check_memory_limit", description=f"Verify {args.limit_gb}GB limit") as s:
                s.measure("limit_gb", args.limit_gb, "GB")
                s.assert_that("limit_positive", args.limit_gb > 0, critical=True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that(f"{args.service}_memory_within_limit", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
