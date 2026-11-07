from age import is_adult


def test_below_18_is_not_adult():
    assert is_adult(17) is False


def test_18_is_adult():
    assert is_adult(18) is True


def test_over_18_is_adult():
    assert is_adult(19) is True
