"""Mock payment region test for matrix testing demo.

This test simulates checking payment processing for a region/currency.
Arguments are baked in by the matrix_test_set macro.
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--currency", required=True)
    args = parser.parse_args()

    print(f"Testing payment processing: region={args.region}, currency={args.currency}")
    # Simulate a passing test
    assert args.region, "Region must be specified"
    assert args.currency, "Currency must be specified"
    print(f"PASS: Payments process correctly in {args.region} with {args.currency}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
