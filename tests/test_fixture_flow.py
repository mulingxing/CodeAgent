import shutil

from codeagent.runner import FIXTURE, run_fixture_tests, safe_candidate


def test_sample_fails_before_fix_and_passes_afterwards(tmp_path):
    for name in ("age.py", "test_age.py"):
        shutil.copy2(FIXTURE / name, tmp_path / name)
    before = run_fixture_tests(tmp_path)
    assert before.returncode == 1
    assert "1 failed" in before.stdout

    corrected = "def is_adult(age: int) -> bool:\n    return age >= 18\n"
    assert safe_candidate(corrected)
    (tmp_path / "age.py").write_text(corrected, encoding="utf-8")
    assert run_fixture_tests(tmp_path).returncode == 0
