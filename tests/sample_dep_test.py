"""A sample dependent test to be wrapped by test_set_test."""


def test_dependent():
    """A test that depends on sample_test passing first."""
    assert 2 + 2 == 4


if __name__ == "__main__":
    test_dependent()
    print("Dependent test passed!")
