"""A sample test to be wrapped by test_set_test."""

import sys


def test_sample():
    """A simple test that always passes."""
    assert 1 + 1 == 2


def test_python_version():
    """Verify Python 3.12."""
    assert sys.version_info.major == 3
    assert sys.version_info.minor == 12


if __name__ == "__main__":
    test_sample()
    test_python_version()
    print("All tests passed!")
