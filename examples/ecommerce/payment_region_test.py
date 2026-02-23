"""Parameterized payment region test.

Accepts --region and --currency via command-line arguments.
Simulates payment processing for the specified region
using the tst_sdk structured logging.
"""

import argparse
import sys
import time

from tst_sdk import test_run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--currency", required=True)
    args = parser.parse_args()

    with test_run() as t:
        # --- Stimulation ---
        with t.block("stimulation", description=f"Process payment in {args.region}") as b:
            with b.step("process_payment", description=f"Charge in {args.currency}") as s:
                time.sleep(0.01)
                s.measure("region", args.region, "")
                s.measure("currency", args.currency, "")
                s.measure("processing_time_ms", 95, "ms")

        # --- Verdict ---
        with t.block("verdict") as b:
            b.assert_that(f"payment_{args.region}", True)

        return t.exit_code()


if __name__ == "__main__":
    sys.exit(main())
