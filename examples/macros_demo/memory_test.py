"""Mock memory limit test for parameterization demo.

This test simulates checking memory limits for a service.
Arguments are baked in by the parameterized_test_set macro.
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--limit-gb", type=float, required=True)
    args = parser.parse_args()

    print(f"Checking memory limit for {args.service}: {args.limit_gb}GB")
    # Simulate a passing test
    assert args.limit_gb > 0, f"Limit must be positive, got {args.limit_gb}"
    print(f"PASS: {args.service} memory within {args.limit_gb}GB limit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
