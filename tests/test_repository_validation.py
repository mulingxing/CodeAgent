from __future__ import annotations

import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from codeagent import config, git_repos, repo_runner, sandbox, sandbox_api
from codeagent.api import create_app
from codeagent.database import make_engine, make_session_factory
from codeagent.models import Base
from codeagent.tasks import process_task


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(git_repos, "LOCAL_REPOS_ROOT", tmp_path / "local_repos")
    monkeypatch.setattr(git_repos, "REVIEWS_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(repo_runner, "DEEPSEEK_API_KEY", "test-placeholder")
    repo = tmp_path / "local_repos" / "sample"
    (repo / "src" / "demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "src" / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "demo" / "stock.py").write_text(
        "def remaining(stock, sold, reserved):\n    return stock - sold\n", encoding="utf-8"
    )
    (repo / "src" / "demo" / "main.py").write_text("print(2 * 3)\n", encoding="utf-8")
    (repo / "tests" / "test_stock.py").write_text(
        "from demo.stock import remaining\n\ndef test_reserved():\n"
        "    assert remaining(10, 2, 3) == 5\n", encoding="utf-8"
    )
    (repo / "tests" / "test_checkout.py").write_text(
        "from demo.stock import remaining\n\ndef test_all_stock():\n"
        "    assert remaining(10, 2, 0) == 8\n", encoding="utf-8"
    )
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@local.invalid", "commit", "-m", "bug")
    return repo


def db(tmp_path: Path):
    engine = make_engine(f"sqlite:///{tmp_path / 'validation.db'}")
    Base.metadata.create_all(engine)
    return engine, make_session_factory(engine)


def test_run_mode_without_pytest_uses_expected_output(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch)
    engine, sessions = db(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _id: "queued-test")) as client:
        repos = client.get("/api/v1/repositories").json()
        assert "src/demo/main.py" in repos[0]["source_files"]
        submitted = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "validation_mode": "run", "run_path": "src/demo/main.py",
            "expected_output": "8", "prompt": "Fix the multiplication output to eight.",
        })
        assert submitted.status_code == 202, submitted.text
        task = submitted.json()
        assert task["validation_mode"] == "run" and task["test_path"] is None

        async def fake_agent(workspace, _prompt, _failure, _editable, _selected, _cancel):
            (workspace / "src" / "demo" / "main.py").write_text("print(2 * 4)\n", encoding="utf-8")
            return 2

        monkeypatch.setattr(repo_runner, "_run_repo_agent", fake_agent)
        assert process_task(task["id"], session_factory=sessions)["status"] == "succeeded"
        result = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert result["validation_status"] == "passed"
        assert "6" in result["baseline_output"]
        assert "8" in result["test_output"]
        assert "src/demo/main.py" in result["diff"]
    engine.dispose()


def test_run_without_expectation_and_manual_are_unverified(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    baseline_commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    async def fake_agent(workspace, _prompt, _failure, _editable, selected, _cancel):
        path = "src/demo/main.py" if selected == "src/demo/main.py" else "src/demo/stock.py"
        if path.endswith("main.py"):
            (workspace / path).write_text("print(2 * 4)\n", encoding="utf-8")
        else:
            (workspace / path).write_text(
                "def remaining(stock, sold, reserved):\n    return stock - sold - reserved\n", encoding="utf-8"
            )
        return 1

    monkeypatch.setattr(repo_runner, "_run_repo_agent", fake_agent)
    run = repo_runner.run_repository_repair(
        "run-unverified", "sample", baseline_commit, None, "Improve the output calculation.",
        validation_mode="run", run_path="src/demo/main.py",
    )
    assert run.validation_status == "unverified" and "8" in run.test_output
    manual = repo_runner.run_repository_repair(
        "manual-unverified", "sample", baseline_commit, None, "Correct the stock calculation.",
        validation_mode="manual",
    )
    assert manual.validation_status == "unverified"
    assert "syntax checks" in manual.test_output


def test_run_expected_output_must_come_from_stdout(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch)
    engine, sessions = db(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _id: "queued-test")) as client:
        submitted = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "validation_mode": "run", "run_path": "src/demo/main.py",
            "expected_output": "8", "prompt": "Correct the output of the main script.",
        })
        assert submitted.status_code == 202, submitted.text
        task_id = submitted.json()["id"]

        async def stderr_only_agent(workspace, _prompt, _failure, _editable, _selected, _cancel):
            (workspace / "src" / "demo" / "main.py").write_text(
                'import sys\nprint("8", file=sys.stderr)\n', encoding="utf-8"
            )
            return 1

        monkeypatch.setattr(repo_runner, "_run_repo_agent", stderr_only_agent)
        assert process_task(task_id, session_factory=sessions)["status"] == "failed"
        result = client.get(f"/api/v1/tasks/{task_id}").json()
        assert result["error_code"] == "run_failed"
        assert result["validation_status"] == "failed"
        assert "--- stderr ---\n8" in result["test_output"]
        assert "Expected stdout to contain: 8" in result["test_output"]
    engine.dispose()


def test_remote_script_response_keeps_stderr_separate(tmp_path, monkeypatch):
    workspace = tmp_path / ".runtime" / "jobs" / "sample-job"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "ROOT", tmp_path)
    monkeypatch.setattr(sandbox_api, "ROOT", tmp_path)
    monkeypatch.setattr(
        sandbox_api, "run_python_script",
        lambda _workspace, _path, _args: subprocess.CompletedProcess(
            args=["python", "main.py"], returncode=0, stdout="normal output", stderr="expected marker"
        ),
    )
    with TestClient(sandbox_api.app) as client:
        class LocalOpener:
            def open(self, request, timeout):
                response = client.post("/run-script", content=request.data,
                                       headers={"Content-Type": "application/json"})
                assert response.status_code == 200, response.text
                return io.BytesIO(response.content)

        monkeypatch.setattr(sandbox, "build_opener", lambda _handler: LocalOpener())
        result = sandbox._run_remote_script(workspace, "main.py", [])
    assert result.stdout == "normal output"
    assert result.stderr == "expected marker"


@pytest.mark.parametrize("post_returncode,post_summary", [(0, "1 passed"), (1, "1 failed")])
def test_long_pytest_output_preserves_final_validation_evidence(
    tmp_path, monkeypatch, post_returncode, post_summary
):
    repo = make_repo(tmp_path, monkeypatch)
    baseline_commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    calls = 0
    warnings = "PydanticDeprecatedSince20: repeated warning\n" * 800

    def long_test_result(_workspace, _paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            output = warnings + "AssertionError: expected 5\n1 failed, 1 warning in 0.12s\n"
            returncode = 1
        else:
            output = warnings + f"{post_summary}, 1 warning in 0.08s\n"
            returncode = post_returncode
        return subprocess.CompletedProcess(
            args=["pytest"], returncode=returncode,
            stdout=sandbox._bounded_output(output), stderr="",
        )

    async def fake_agent(workspace, _prompt, _failure, _editable, _selected, _cancel):
        (workspace / "src" / "demo" / "stock.py").write_text(
            "def remaining(stock, sold, reserved):\n    return stock - sold - reserved\n",
            encoding="utf-8",
        )
        return 1

    monkeypatch.setattr(repo_runner, "run_fixture_tests", long_test_result)
    monkeypatch.setattr(repo_runner, "_run_repo_agent", fake_agent)
    if post_returncode == 0:
        result = repo_runner.run_repository_repair(
            "long-pytest-pass", "sample", baseline_commit, "tests/test_stock.py",
            "Fix the reserved stock calculation.",
        )
        final_output = result.test_output
        baseline_output = result.baseline_output
    else:
        with pytest.raises(repo_runner.AgentExecutionError) as failed:
            repo_runner.run_repository_repair(
                "long-pytest-fail", "sample", baseline_commit, "tests/test_stock.py",
                "Fix the reserved stock calculation.",
            )
        final_output = failed.value.test_output
        baseline_output = failed.value.baseline_output
        assert "return stock - sold - reserved" in failed.value.diff
    assert calls == (2 if post_returncode == 0 else 3)
    assert len(baseline_output) <= repo_runner.BASELINE_OUTPUT_LIMIT
    assert "AssertionError: expected 5" in baseline_output
    assert "1 failed, 1 warning" in baseline_output
    assert len(final_output) <= repo_runner.RESULT_OUTPUT_LIMIT
    assert "--- before repair ---" in final_output
    assert f"--- after repair, pass {1 if post_returncode == 0 else 2} ---" in final_output
    assert post_summary in final_output
    assert final_output.index("--- after repair") < final_output.rindex(post_summary)


@pytest.mark.parametrize("second_edit", ["undo", "oversize"])
def test_second_pass_without_reviewable_patch_retains_first_candidate(tmp_path, monkeypatch, second_edit):
    make_repo(tmp_path, monkeypatch)
    engine, sessions = db(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _id: "queued-test")) as client:
        submitted = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "test_path": "tests/test_stock.py",
            "prompt": "Repair the calculation while keeping the tests unchanged.",
        })
        assert submitted.status_code == 202, submitted.text
        task_id = submitted.json()["id"]
        attempts = 0

        async def unreviewable_retry(workspace, _prompt, _failure, _editable, _selected, _cancel):
            nonlocal attempts
            attempts += 1
            stock = workspace / "src" / "demo" / "stock.py"
            if attempts == 1:
                stock.write_text(
                    "def remaining(stock, sold, reserved):\n    return stock - sold - 1\n",
                    encoding="utf-8",
                )
            elif second_edit == "undo":
                stock.write_text(
                    "def remaining(stock, sold, reserved):\n    return stock - sold\n",
                    encoding="utf-8",
                )
            else:
                stock.write_text("#" + "x" * 70_000 + "\n", encoding="utf-8")
                (workspace / "src" / "demo" / "main.py").write_text(
                    "#" + "y" * 70_000 + "\n", encoding="utf-8"
                )
            return 1

        monkeypatch.setattr(repo_runner, "_run_repo_agent", unreviewable_retry)
        assert process_task(task_id, session_factory=sessions)["status"] == "failed"
        result = client.get(f"/api/v1/tasks/{task_id}").json()
        assert attempts == 2
        assert result["error_code"] == "validation_failed"
        assert result["validation_status"] == "failed"
        assert "return stock - sold - 1" in result["diff"]
        assert "x" * 100 not in result["diff"]
        assert "pass 1" in result["test_output"]
    engine.dispose()


def test_multiple_pytests_retry_once_and_preserve_failed_candidate(tmp_path, monkeypatch):
    make_repo(tmp_path, monkeypatch)
    engine, sessions = db(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _id: "queued-test")) as client:
        submitted = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "validation_mode": "pytest",
            "test_paths": ["tests/test_stock.py", "tests/test_checkout.py"],
            "prompt": "Account for reserved stock in the remaining count.",
        })
        assert submitted.status_code == 202, submitted.text
        task = submitted.json()
        assert task["test_paths"] == ["tests/test_stock.py", "tests/test_checkout.py"]
        calls = []

        async def retry_agent(workspace, _prompt, _failure, _editable, selected, _cancel):
            calls.append(selected)
            text = ("def remaining(stock, sold, reserved):\n"
                    "    return stock - sold - 1\n" if len(calls) == 1 else
                    "def remaining(stock, sold, reserved):\n"
                    "    return stock - sold - reserved\n")
            (workspace / "src" / "demo" / "stock.py").write_text(text, encoding="utf-8")
            return 2

        monkeypatch.setattr(repo_runner, "_run_repo_agent", retry_agent)
        assert process_task(task["id"], session_factory=sessions)["status"] == "succeeded"
        result = client.get(f"/api/v1/tasks/{task['id']}").json()
        assert len(calls) == 2 and all(isinstance(value, list) for value in calls)
        assert result["agent_turns"] == 4
        assert result["validation_status"] == "passed"
        assert "pass 2" in result["test_output"]

        async def wrong_agent(workspace, _prompt, _failure, _editable, _selected, _cancel):
            (workspace / "src" / "demo" / "stock.py").write_text(
                "def remaining(stock, sold, reserved):\n    return stock - sold - 1\n", encoding="utf-8"
            )
            return 1

        monkeypatch.setattr(repo_runner, "_run_repo_agent", wrong_agent)
        failed_task = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "test_path": "tests/test_stock.py",
            "prompt": "Correct reserved stock without changing the tests.",
        }).json()
        assert process_task(failed_task["id"], session_factory=sessions)["status"] == "failed"
        failed = client.get(f"/api/v1/tasks/{failed_task['id']}").json()
        assert failed["validation_status"] == "failed"
        assert "return stock - sold - 1" in failed["diff"]
        assert "pass 2" in failed["test_output"]
        assert client.post(
            f"/api/v1/tasks/{failed_task['id']}/review", headers={"X-CodeAgent-Action": "review"}
        ).status_code == 409

        attempts = 0

        async def interrupted_retry(workspace, _prompt, _failure, _editable, _selected, _cancel):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise TimeoutError("second pass stalled")
            (workspace / "src" / "demo" / "stock.py").write_text(
                "def remaining(stock, sold, reserved):\n    return stock - sold - 1\n", encoding="utf-8"
            )
            return 1

        monkeypatch.setattr(repo_runner, "_run_repo_agent", interrupted_retry)
        interrupted = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "test_path": "tests/test_stock.py",
            "prompt": "Repair the wrong stock result after validation.",
        }).json()
        assert process_task(interrupted["id"], session_factory=sessions)["status"] == "failed"
        saved = client.get(f"/api/v1/tasks/{interrupted['id']}").json()
        assert saved["error_code"] == "validation_failed"
        assert saved["validation_status"] == "failed"
        assert "return stock - sold - 1" in saved["diff"]
        assert "pass 1" in saved["test_output"]
    engine.dispose()


def test_migration_from_existing_tasks_keeps_old_rows(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    alembic = AlembicConfig(str(root / "alembic.ini"))
    alembic.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(alembic, "0005_review_commit")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tasks (id, repo_id, source_repo, prompt, status, phase, attempts, created_at) "
            "VALUES (:id, :repo, :source, :prompt, :status, :phase, :attempts, :created)"
        ), {
            "id": "old-task", "repo": "sample", "source": "sample", "prompt": "Old repair task",
            "status": "succeeded", "phase": "succeeded", "attempts": 1,
            "created": datetime.now(timezone.utc),
        })
        connection.execute(text(
            "INSERT INTO tasks (id, repo_id, source_repo, prompt, status, phase, attempts, error_code, created_at) "
            "VALUES (:id, :repo, :source, :prompt, :status, :phase, :attempts, :error, :created)"
        ), {
            "id": "old-worker-error", "repo": "sample", "source": "sample", "prompt": "Old failed task",
            "status": "failed", "phase": "failed", "attempts": 1, "error": "worker_error",
            "created": datetime.now(timezone.utc),
        })
    engine.dispose()
    command.upgrade(alembic, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        old = connection.execute(text(
            "SELECT id, validation_mode, validation_status, test_paths "
            "FROM tasks WHERE id = 'old-task'"
        )).one()
    assert old.id == "old-task"
    assert old.validation_mode == "pytest"
    assert old.validation_status == "passed"
    assert old.test_paths is None
    with engine.connect() as connection:
        worker_error = connection.execute(text(
            "SELECT validation_status FROM tasks WHERE id = 'old-worker-error'"
        )).scalar_one()
    assert worker_error is None
    engine.dispose()
