"""Trivial Python script to validate the hermetic Python 3.12 toolchain."""

import sys


def main():
    version = sys.version_info
    print(f"Hello from Python {version.major}.{version.minor}.{version.micro}")
    assert version.major == 3, f"Expected Python 3, got {version.major}"
    assert version.minor == 12, f"Expected Python 3.12, got 3.{version.minor}"
    print("Toolchain validation passed!")


if __name__ == "__main__":
    main()
