from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from codeagent import git_repos, repo_runner
from codeagent.database import make_engine, make_session_factory
from codeagent.models import Base
from codeagent.api import create_app
from fastapi.testclient import TestClient


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(git_repos, "LOCAL_REPOS_ROOT", tmp_path / "local_repos")
    monkeypatch.setattr(git_repos, "REVIEWS_ROOT", tmp_path / "reviews")
    repo = tmp_path / "local_repos" / "sample"
    (repo / "src" / "demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "src" / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "demo" / "stock.py").write_text("def remaining(stock, sold, reserved):\n    return stock - sold\n", encoding="utf-8")
    (repo / "tests" / "test_stock.py").write_text(
        "from demo.stock import remaining\n\ndef test_reserved():\n    assert remaining(10, 2, 3) == 5\n", encoding="utf-8"
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@local.invalid", "commit", "-m", "bug")
    return repo


def test_repository_task_review_merge_and_cleanup(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(repo_runner, "DEEPSEEK_API_KEY", "test-placeholder")
    engine = make_engine(f"sqlite:///{tmp_path / 'stage5.db'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _id: "queued-test")) as client:
        repos = client.get("/api/v1/repositories").json()
        assert repos[0]["name"] == "sample" and repos[0]["clean"], (repos, repr(git_repos.git(repo, "status", "--porcelain", "--untracked-files=all")))
        response = client.post("/api/v1/repository-tasks", json={
            "repo_name": "sample", "test_path": "tests/test_stock.py", "prompt": "Account for reserved stock.",
        })
        assert response.status_code == 202, response.text
        task = response.json()
        assert task["base_commit"] == _git(repo, "rev-parse", "HEAD")

        async def fake_agent(workspace, _prompt, _failure, _editable, _test_path, _cancel_check):
            (workspace / "src" / "demo" / "stock.py").write_text(
                "def remaining(stock, sold, reserved):\n    return stock - sold - reserved\n", encoding="utf-8"
            )
            return 2

        monkeypatch.setattr(repo_runner, "_run_repo_agent", fake_agent)
        result = repo_runner.run_repository_repair(task["id"], "sample", task["base_commit"],
                                                   "tests/test_stock.py", task["prompt"])
        assert "1 passed" in result.test_output
        assert "a/src/demo/stock.py" in result.diff
        assert "return stock - sold\n" in (repo / "src" / "demo" / "stock.py").read_text()

        # Persist the successful result, as the real Worker does before review.
        from codeagent.models import Task
        with sessions() as session:
            record = session.get(Task, task["id"])
            record.status = "succeeded"
            record.diff = result.diff
            record.test_output = result.test_output
            session.commit()
        review_response = client.post(f"/api/v1/tasks/{task['id']}/review", headers={"X-CodeAgent-Action": "review"})
        assert review_response.status_code == 200, (review_response.text, repr(result.diff))
        review = review_response.json()
        review_dir = tmp_path / review["review_path"]
        assert review_dir.is_dir()
        assert not Path((review_dir / ".git").read_text().split(": ", 1)[1].strip()).is_absolute()
        assert _git(review_dir, "rev-parse", "HEAD") == review["review_commit"]
        assert "return stock - sold\n" in (repo / "src" / "demo" / "stock.py").read_text()
        merged = client.post(f"/api/v1/tasks/{task['id']}/merge", headers={"X-CodeAgent-Action": "merge"})
        assert merged.status_code == 200, merged.text
        assert merged.json()["review_status"] == "merged"
        assert "stock - sold - reserved" in (repo / "src" / "demo" / "stock.py").read_text()
        assert not review_dir.exists()
        assert _git(repo, "status", "--porcelain") == ""
    engine.dispose()


def test_dirty_repository_is_not_submitted(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, monkeypatch)
    (repo / "scratch.txt").write_text("unsaved", encoding="utf-8")
    with pytest.raises(git_repos.RepositoryError, match="clean working tree"):
        git_repos.validate_submission("sample", "tests/test_stock.py")
