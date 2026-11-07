from __future__ import annotations

import os
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from requests.exceptions import Timeout as RequestsTimeout

from codeagent.config import ROOT, SANDBOX_IMAGE, SANDBOX_SERVICE_URL, SANDBOX_WORKSPACE_VOLUME, TEST_EXECUTION_MODE


class SandboxError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def _bounded_output(output: str, limit: int = 12_000) -> str:
    """Retain the final test summary when a checker prints excessive output."""
    if len(output) <= limit:
        return output
    marker = "\n... middle of output omitted ...\n"
    head = min(1_500, (limit - len(marker)) // 4)
    tail = limit - len(marker) - head
    return output[:head] + marker + output[-tail:]


def run_fixture_tests(workspace: Path, test_path: str | list[str] | None = None) -> subprocess.CompletedProcess[str]:
    if TEST_EXECUTION_MODE == "local":
        return _run_local_tests(workspace, test_path)
    if TEST_EXECUTION_MODE == "docker":
        return _run_docker_tests(workspace, test_path)
    if TEST_EXECUTION_MODE == "remote":
        return _run_remote_tests(workspace, test_path)
    raise SandboxError("invalid_test_mode", "The test execution mode is not supported.")


def _test_filenames(workspace: Path, test_path: str | list[str] | None = None) -> list[str]:
    if test_path is not None:
        paths = [test_path] if isinstance(test_path, str) else test_path
        if not paths or len(paths) > 8 or len(set(paths)) != len(paths):
            raise SandboxError("invalid_test_path", "Select one to eight distinct test files.")
        for path in paths:
            if len(path) > 240 or re.fullmatch(r"tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_-]+\.py", path) is None:
                raise SandboxError("invalid_test_path", "The selected test path is invalid.")
            if not (workspace / path).is_file():
                raise SandboxError("invalid_test_path", "The selected test file is missing.")
        return paths
    if (workspace / "test_solution.py").is_file():
        return ["test_solution.py"]
    if (workspace / "test_age.py").is_file():
        return ["test_age.py"]
    raise SandboxError("invalid_workspace", "The fixed test file is missing.")


def _test_filename(workspace: Path, test_path: str | None = None) -> str:
    """Retain the original single-file helper for callers and tests."""
    return _test_filenames(workspace, test_path)[0]


def _script_command(workspace: Path, run_path: str, run_args: list[str] | None = None) -> list[str]:
    args = run_args or []
    if (
        len(run_path) > 240 or
        re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py", run_path) is None or
        not (workspace / run_path).is_file() or
        len(args) > 8 or any(
            not isinstance(arg, str) or len(arg) > 100 or "\x00" in arg or "\n" in arg or "\r" in arg
            for arg in args
        )
    ):
        raise SandboxError("invalid_run_command", "Select a tracked Python entrypoint with short arguments.")
    return ["python", run_path, *args]


def run_python_script(workspace: Path, run_path: str, run_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    command = _script_command(workspace, run_path, run_args)
    if TEST_EXECUTION_MODE == "local":
        return _run_local_command(workspace, [sys.executable, *command[1:]], separate_stderr=True)
    if TEST_EXECUTION_MODE == "docker":
        return _run_docker_command(workspace, command, "script", separate_stderr=True)
    if TEST_EXECUTION_MODE == "remote":
        return _run_remote_script(workspace, run_path, run_args or [])
    raise SandboxError("invalid_test_mode", "The test execution mode is not supported.")


def _run_local_tests(workspace: Path, test_path: str | list[str] | None = None) -> subprocess.CompletedProcess[str]:
    test_files = _test_filenames(workspace, test_path)
    return _run_local_command(workspace, [sys.executable, "-m", "pytest", "-q", "--disable-warnings",
                                          "--tb=short", *test_files, "-p", "no:cacheprovider"])


def _run_local_command(workspace: Path, command: list[str], *, separate_stderr: bool = False) -> subprocess.CompletedProcess[str]:
    test_env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join([str(workspace), str(workspace / "src")])}
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        test_env.pop(name, None)
    try:
        return subprocess.run(
            command,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT,
            timeout=30,
            check=False,
            env=test_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError("test_timeout", "The sample test exceeded its time limit.") from exc


def _validate_workspace(workspace: Path) -> Path:
    jobs_root = (ROOT / ".runtime" / "jobs").resolve()
    workspace = workspace.resolve()
    if workspace.parent != jobs_root or re.fullmatch(r"[A-Za-z0-9_.-]+", workspace.name) is None:
        raise SandboxError("invalid_workspace", "The test workspace path is invalid.")
    return workspace


def _run_remote_tests(workspace: Path, test_path: str | list[str] | None = None) -> subprocess.CompletedProcess[str]:
    workspace = _validate_workspace(workspace)
    test_files = _test_filenames(workspace, test_path)
    request = Request(
        f"{SANDBOX_SERVICE_URL.rstrip('/')}/run-tests",
        data=json.dumps({"workspace_name": workspace.name, "test_paths": test_files}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=45) as response:
            result = json.load(response)
        return subprocess.CompletedProcess(
            args=["sandbox", workspace.name],
            returncode=int(result["returncode"]),
            stdout=_bounded_output(str(result["output"])),
            stderr="",
        )
    except Exception as exc:
        raise SandboxError("sandbox_unavailable", "The isolated test service is unavailable.") from exc


def _run_remote_script(workspace: Path, run_path: str, run_args: list[str]) -> subprocess.CompletedProcess[str]:
    workspace = _validate_workspace(workspace)
    request = Request(
        f"{SANDBOX_SERVICE_URL.rstrip('/')}/run-script",
        data=json.dumps({"workspace_name": workspace.name, "run_path": run_path, "run_args": run_args}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=45) as response:
            result = json.load(response)
        return subprocess.CompletedProcess(
            args=["sandbox", workspace.name], returncode=int(result["returncode"]),
            stdout=_bounded_output(str(result["output"])),
            stderr=_bounded_output(str(result.get("stderr", ""))),
        )
    except Exception as exc:
        raise SandboxError("sandbox_unavailable", "The isolated script service is unavailable.") from exc


def _run_docker_tests(workspace: Path, test_path: str | list[str] | None = None) -> subprocess.CompletedProcess[str]:
    workspace = _validate_workspace(workspace)
    test_files = _test_filenames(workspace, test_path)
    return _run_docker_command(workspace, ["python", "-m", "pytest", "-q", "--disable-warnings",
                                           "--tb=short", *test_files, "-p", "no:cacheprovider"], "pytest")


def _run_docker_command(workspace: Path, command: list[str], label: str,
                        *, separate_stderr: bool = False) -> subprocess.CompletedProcess[str]:
    workspace = _validate_workspace(workspace)

    # The Worker and test container share only this named volume. The test container
    # gets a read-only mount and no model credentials, network, or host source bind.
    try:
        import docker

        client = docker.from_env(timeout=10)
    except Exception as exc:
        raise SandboxError("sandbox_unavailable", "The Docker test runner is unavailable.") from exc

    container = None
    try:
        mount_dir = f"/workspaces/{workspace.name}"
        container = client.containers.run(
            SANDBOX_IMAGE,
            command=command,
            detach=True,
            volumes={SANDBOX_WORKSPACE_VOLUME: {"bind": "/workspaces", "mode": "ro"}},
            working_dir=mount_dir,
            environment={
                "PYTHONPATH": f"{mount_dir}:{mount_dir}/src",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "HOME": "/tmp",
            },
            network_mode="none",
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
            user="10001:10001",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            pids_limit=64,
            mem_limit="256m",
            nano_cpus=1_000_000_000,
        )
        result = container.wait(timeout=35)
        output = _bounded_output(container.logs(stdout=True, stderr=not separate_stderr).decode("utf-8", errors="replace"))
        error_output = (_bounded_output(container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace"))
                        if separate_stderr else "")
        return subprocess.CompletedProcess(
            args=["docker", label, workspace.name],
            returncode=int(result["StatusCode"]),
            stdout=output,
            stderr=error_output,
        )
    except RequestsTimeout as exc:
        raise SandboxError("test_timeout", "The isolated sample test exceeded its time limit.") from exc
    except Exception as exc:
        raise SandboxError("sandbox_failed", "The isolated test container could not complete.") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        client.close()
