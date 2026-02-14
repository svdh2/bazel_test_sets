"""Simulated user registration test -- no structured logging."""

import sys
import time


def main() -> int:
    print("Creating user: testuser@example.com")
    time.sleep(0.01)
    print("Password hash: OK")
    print("Email validation: OK")
    print("User created successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
