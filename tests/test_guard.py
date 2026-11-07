from codeagent.runner import safe_candidate


def test_guard_accepts_simple_safe_fix():
    assert safe_candidate("def is_adult(age: int) -> bool:\n    return age >= 18\n")


def test_guard_rejects_extra_executable_code():
    assert not safe_candidate("import os\ndef is_adult(age: int) -> bool:\n    return age >= 18\n")


def test_guard_rejects_unchanged_bug():
    assert not safe_candidate("def is_adult(age: int) -> bool:\n    return age > 18\n")


def test_guard_rejects_encoding_cookie():
    assert not safe_candidate("# coding: utf-7\ndef is_adult(age):\n    return age >= 18\n")
