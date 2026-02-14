"""Simulated shipping calculation test -- no structured logging."""

import sys
import time


def main() -> int:
    print("Calculating shipping for order #12345")
    time.sleep(0.01)
    print("Weight: 2.5 kg")
    print("Destination: US-CA")
    print("Shipping cost: $7.99")
    print("Estimated delivery: 3-5 business days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
