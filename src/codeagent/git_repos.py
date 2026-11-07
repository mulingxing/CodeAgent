"""A deliberately narrow local Git boundary for repository repairs."""

from __future__ import annotations

import re
import os
import subprocess
from pathlib import Path

from codeagent.config import LOCAL_REPOS_ROOT, REVIEWS_ROOT


class RepositoryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def git(repo: Path, *args: str, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "-c", "core.autocrlf=input", "-C", str(repo), *args],
            input=input_text.encode("utf-8") if input_text is not None else None,
            capture_output=True, timeout=20, check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryError("git_unavailable", "Git could not complete the repository operation.") from exc
    if result.returncode:
        raise RepositoryError("git_failed", (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()[:400] or "Git command failed.")
    return result.stdout.decode("utf-8", errors="replace").strip()


def repo_path(name: str) -> Path:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}", name) is None:
        raise RepositoryError("invalid_repo", "The repository name is invalid.")
    root = LOCAL_REPOS_ROOT.resolve()
    path = root / name
    if not path.is_dir() or path.is_symlink() or not (path / ".git").is_dir():
        raise RepositoryError("repo_missing", "Place a local Git repository in the local_repos folder first.")
    if path.resolve().parent != root or Path(git(path, "rev-parse", "--show-toplevel")).resolve() != path.resolve():
        raise RepositoryError("invalid_repo", "The repository is outside the allowed folder.")
    return path


def inspect_repo(name: str) -> dict:
    path = repo_path(name)
    branch = git(path, "branch", "--show-current")
    commit = git(path, "rev-parse", "HEAD")
    clean = not bool(git(path, "status", "--porcelain", "--untracked-files=all"))
    tracked = git(path, "ls-files").splitlines()
    tests = [p for p in tracked
             if re.fullmatch(r"tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_-]+\.py", p)]
    source_files = [
        p for p in tracked
        if re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py", p)
        and not Path(p).name.startswith("test_")
        and all(part and not part.startswith(".") for part in Path(p).parts)
        and Path(p).parts[0] not in {"tests", "migrations", "venv", "site-packages"}
        and (path / p).is_file() and not (path / p).is_symlink()
        and (path / p).stat().st_size <= 100_000
    ]
    return {"name": name, "branch": branch, "commit": commit, "clean": clean,
            "tests": tests, "source_files": source_files}


def list_repos() -> list[dict]:
    LOCAL_REPOS_ROOT.mkdir(parents=True, exist_ok=True)
    found = []
    for child in sorted(LOCAL_REPOS_ROOT.iterdir()):
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}", child.name):
            try:
                found.append(inspect_repo(child.name))
            except RepositoryError:
                pass
    return found


def validate_submission(name: str, test_path: str | None = None, *,
                        validation_mode: str = "pytest", test_paths: list[str] | None = None,
                        run_path: str | None = None) -> dict:
    info = inspect_repo(name)
    if not info["branch"] or not info["clean"]:
        raise RepositoryError("repo_not_ready", "The repository needs a checked-out branch and a clean working tree.")
    if validation_mode == "pytest":
        paths = test_paths or ([test_path] if test_path else [])
        if not paths or any(path not in info["tests"] for path in paths):
            raise RepositoryError("test_missing", "Select tracked pytest files from this repository.")
    elif validation_mode == "run":
        if run_path not in info["source_files"]:
            raise RepositoryError("run_path_missing", "Select a tracked Python source file to run.")
    elif validation_mode != "manual":
        raise RepositoryError("invalid_validation_mode", "The validation mode is not supported.")
    return info


def _review_location(name: str, task_id: str) -> tuple[str, Path]:
    branch = f"codeagent/{task_id}"
    root = REVIEWS_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}-{task_id[:8]}"
    if path.exists() or path.is_symlink():
        raise RepositoryError("review_exists", "The review folder already exists.")
    return branch, path


def create_review(name: str, task_id: str, base_commit: str, base_branch: str, patch: str) -> tuple[str, str, str]:
    repo = repo_path(name)
    info = inspect_repo(name)
    if not info["clean"] or info["commit"] != base_commit or info["branch"] != base_branch:
        raise RepositoryError("repo_changed", "The original repository changed. Restore its recorded commit and clean state before review.")
    if not patch or len(patch.encode("utf-8")) > 200_000:
        raise RepositoryError("invalid_patch", "The repair patch is empty or too large.")
    branch, review = _review_location(name, task_id)
    git(repo, "worktree", "add", "--relative-paths", "-b", branch, str(review), base_commit)
    try:
        git(review, "apply", "--cached", "--check", "--ignore-whitespace", "--whitespace=nowarn", "-", input_text=patch)
        git(review, "apply", "--cached", "--ignore-whitespace", "--whitespace=nowarn", "-", input_text=patch)
        git(review, "checkout-index", "--all", "--force")
        if not git(review, "diff", "--cached", "--name-only"):
            raise RepositoryError("empty_patch", "The patch made no changes.")
        git(review, "-c", "user.name=CodeAgent", "-c", "user.email=codeagent@local.invalid", "commit", "-m", f"CodeAgent repair {task_id[:8]}")
    except Exception:
        try:
            git(repo, "worktree", "remove", "--force", str(review))
            git(repo, "branch", "-D", branch)
        except RepositoryError:
            pass
        raise
    return branch, f"reviews/{review.name}", git(review, "rev-parse", "HEAD")


def merge_review(name: str, base_commit: str, base_branch: str, branch: str, review_commit: str, relative_review: str) -> bool:
    repo = repo_path(name)
    review = (REVIEWS_ROOT / Path(relative_review).name).resolve()
    if relative_review != f"reviews/{review.name}" or review.parent != REVIEWS_ROOT.resolve() or not review.is_dir():
        raise RepositoryError("review_missing", "The review worktree is missing.")
    if not branch.startswith("codeagent/") or not git(repo, "status", "--porcelain", "--untracked-files=all") == "":
        raise RepositoryError("repo_not_ready", "The original repository must be clean before merging.")
    if git(repo, "rev-parse", "HEAD") != base_commit:
        raise RepositoryError("repo_changed", "The original branch has moved since the repair.")
    if git(repo, "branch", "--show-current") != base_branch:
        raise RepositoryError("repo_changed", "The original repository is on a different branch.")
    if git(review, "status", "--porcelain", "--untracked-files=all"):
        raise RepositoryError("review_changed", "The review worktree has uncommitted changes.")
    if git(review, "branch", "--show-current") != branch:
        raise RepositoryError("review_changed", "The review worktree is on a different branch.")
    if git(review, "rev-parse", "HEAD") != review_commit:
        raise RepositoryError("review_changed", "The review branch changed after it was prepared.")
    if git(repo, "merge-base", branch, "HEAD") != base_commit:
        raise RepositoryError("review_changed", "The review branch no longer starts from the recorded commit.")
    git(repo, "merge", "--ff-only", branch)
    try:
        git(repo, "worktree", "remove", str(review))
        git(repo, "branch", "-d", branch)
        return True
    except RepositoryError:
        return False
