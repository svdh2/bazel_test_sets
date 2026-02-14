"""Simulated shopping cart test -- no structured logging (graceful degradation)."""

import sys
import time


def main() -> int:
    print("Adding item SKU-001 to cart...")
    time.sleep(0.01)
    print("Cart total: $29.99")
    print("Adding item SKU-002 to cart...")
    time.sleep(0.01)
    print("Cart total: $54.98")
    print("Cart validation: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
