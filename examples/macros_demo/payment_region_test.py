"""Mock payment region test for matrix testing demo.

This test simulates checking payment processing for a region/currency.
Arguments are baked in by the matrix_test_set macro.
Uses the tst_sdk structured logging.
"""

import argparse
import sys

from tst_sdk import test_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--currency", required=True)
    args = parser.parse_args()

    with test_run() as t:
        # --- Stimulation ---
        with t.block("stimulation", description=f"Test payments in {args.region}") as b:
            with b.step("process_payment", description=f"Process {args.currency} payment") as s:
                s.measure("region", args.region, "")
                s.measure("currency", args.currency, "")
                s.assert_that("region_specified", bool(args.region), critical=True)
                s.assert_that("currency_specified", bool(args.currency), critical=True)

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that(f"payment_{args.region}_{args.currency}", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
