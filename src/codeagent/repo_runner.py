"""Repair a tracked Python repository snapshot without executing it in the Worker."""

from __future__ import annotations

import asyncio
import difflib
import io
import re
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from codeagent.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, ROOT, TEST_EXECUTION_MODE
from codeagent.git_repos import RepositoryError, git, repo_path
from codeagent.runner import AgentExecutionError, RepairResult
from codeagent.sandbox import SandboxError, run_fixture_tests, run_python_script

MAX_ARCHIVE_BYTES = 8_000_000
MAX_FILES = 500
MAX_PATCH_BYTES = 120_000
MAX_SOURCE_BYTES = 100_000
MAX_REPLACEMENT_BYTES = 8_000
MAX_REPAIR_PASSES = 2
REPOSITORY_TASK_SECONDS = 300
POST_VALIDATION_RESERVE_SECONDS = 50
RESULT_OUTPUT_LIMIT = 12_000
BASELINE_OUTPUT_LIMIT = 4_000
BASELINE_IN_RESULT_LIMIT = 2_500


def _excerpt(output: str, limit: int) -> str:
    """Show the beginning and the final pytest result within a fixed budget."""
    if len(output) <= limit:
        return output
    marker = "\n... middle of output omitted ...\n"
    head = min(500, (limit - len(marker)) // 4)
    return output[:head] + marker + output[-(limit - len(marker) - head):]


def _visible_output(result: subprocess.CompletedProcess[str], limit: int = RESULT_OUTPUT_LIMIT) -> str:
    """Keep stderr visible without treating it as expected stdout."""
    if not result.stderr:
        return _excerpt(result.stdout, limit)
    separator = "\n--- stderr ---\n"
    stderr_budget = min(len(result.stderr), 2_500)
    stdout_budget = limit - len(separator) - stderr_budget
    return (_excerpt(result.stdout, stdout_budget) + separator
            + _excerpt(result.stderr, stderr_budget))


def _validation_output(baseline_output: str | None, post_output: str, attempt: int) -> str:
    before = (f"--- before repair ---\n{_excerpt(baseline_output, BASELINE_IN_RESULT_LIMIT)}\n"
              if baseline_output is not None else "")
    after_header = f"--- after repair, pass {attempt} ---\n"
    post_budget = RESULT_OUTPUT_LIMIT - len(before) - len(after_header)
    return before + after_header + _excerpt(post_output, post_budget)


def _snapshot(repo: Path, commit: str, workspace: Path) -> dict[str, bytes]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise AgentExecutionError("invalid_commit", "The recorded Git commit is invalid.")
    try:
        archive = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(repo), "archive", "--format=tar", commit],
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgentExecutionError("snapshot_failed", "Could not snapshot the Git repository.") from exc
    if archive.returncode or len(archive.stdout) > MAX_ARCHIVE_BYTES:
        raise AgentExecutionError("snapshot_failed", "The repository snapshot is unavailable or too large (8 MB limit).")
    originals: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            for entry in tar:
                if entry.isdir():
                    continue
                path = Path(entry.name)
                if path.parts[0].startswith("."):
                    continue
                if (not entry.isfile() or path.is_absolute() or ".." in path.parts or
                    len(originals) >= MAX_FILES or entry.size > 250_000 or
                    sum(len(v) for v in originals.values()) + entry.size > MAX_ARCHIVE_BYTES):
                    raise AgentExecutionError("snapshot_unsupported", "The repository contains an unsupported path or exceeds the snapshot limit.")
                source = tar.extractfile(entry)
                if source is None:
                    raise AgentExecutionError("snapshot_failed", "Could not read a tracked file.")
                data = source.read()
                destination = workspace / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                originals[path.as_posix()] = data
    except (tarfile.TarError, OSError) as exc:
        raise AgentExecutionError("snapshot_failed", "Could not unpack the Git snapshot.") from exc
    return originals


def _editable(path: str, data: bytes) -> bool:
    parts = Path(path).parts
    return (
        path.endswith(".py") and len(data) <= MAX_SOURCE_BYTES and
        all(not item.startswith(".") for item in parts) and
        parts[0] not in {"tests", "migrations", "venv", "site-packages"} and
        not Path(path).name.startswith("test_") and
        b"\x00" not in data
    )


def _replace_source_snippet(workspace: Path, editable: dict[str, bytes],
                            path: str, old: str, new: str) -> str | None:
    """Apply one bounded, unambiguous edit to an allowed Python source file."""
    if path not in editable:
        return "The path is outside the editable Python source boundary."
    try:
        old_size, new_size = len(old.encode("utf-8")), len(new.encode("utf-8"))
    except UnicodeError:
        return "The replacement must be valid UTF-8 text."
    if (not old or old == new or old_size > MAX_REPLACEMENT_BYTES or
            new_size > MAX_REPLACEMENT_BYTES or "\x00" in new):
        return "Use a non-empty, changed snippet of at most 8 KB per old/new value."
    source_path = workspace / path
    try:
        source = source_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        return "Could not read the current Python source file."
    if "\r\n" in source and source.count("\r\n") == source.count("\n"):
        # The tool may receive LF snippets even when the tracked file uses CRLF.
        old = old.replace("\r\n", "\n").replace("\n", "\r\n")
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        if len(old.encode("utf-8")) > MAX_REPLACEMENT_BYTES or len(new.encode("utf-8")) > MAX_REPLACEMENT_BYTES:
            return "The replacement exceeds the 8 KB limit after preserving line endings."
    if old == new:
        return "The replacement must change the source file."
    first = source.find(old)
    if first < 0:
        return "The old snippet was not found; read the current file and try again."
    if source.find(old, first + 1) >= 0:
        return "The old snippet is not unique; include more surrounding context."
    candidate = source[:first] + new + source[first + len(old):]
    candidate_bytes = candidate.encode("utf-8")
    if len(candidate_bytes) > MAX_SOURCE_BYTES:
        return "The edited source file exceeds the 100 KB limit."
    try:
        compile(candidate, path, "exec")
    except (SyntaxError, ValueError):
        return "Python syntax is invalid; revise the replacement."
    try:
        source_path.write_bytes(candidate_bytes)
    except OSError:
        return "Could not save the edited Python source file."
    return None


async def _run_repo_agent(workspace: Path, prompt: str, failure: str, editable: dict[str, bytes],
                          test_path: str | list[str] | None, cancel_check=None) -> int:
    from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, create_sdk_mcp_server, query, tool

    touched: set[str] = set()
    final: ResultMessage | None = None
    selected_files = [test_path] if isinstance(test_path, str) else (test_path or [])

    @tool("list_files", "List tracked Python source files available for repair and the selected read-only test.", {})
    async def list_files(_args):
        selected = "\nSelected validation files:\n" + "\n".join(selected_files) if selected_files else ""
        available = "\n".join(f"{path} ({len(editable[path])} bytes)" for path in sorted(editable))
        return {"content": [{"type": "text", "text": "Editable:\n" + available + selected}]}

    @tool("read_file", "Read one listed Python source file.", {"path": str})
    async def read_file(args):
        path = args["path"]
        if path not in editable and path not in selected_files:
            return {"content": [{"type": "text", "text": "Path is not in the allowed source/test list."}], "is_error": True}
        return {"content": [{"type": "text", "text": (workspace / path).read_bytes().decode("utf-8")}]}

    @tool("write_file", "Replace one listed Python source file. Test files cannot be changed.", {"path": str, "content": str})
    async def write_file(args):
        path, content = args["path"], args["content"]
        if path not in editable or len(content.encode("utf-8")) > MAX_SOURCE_BYTES or "\x00" in content:
            return {"content": [{"type": "text", "text": "The path or content is outside the allowed source boundary."}], "is_error": True}
        try:
            compile(content, path, "exec")
        except (SyntaxError, ValueError):
            return {"content": [{"type": "text", "text": "Python syntax is invalid; revise the content."}], "is_error": True}
        (workspace / path).write_bytes(content.encode("utf-8"))
        touched.add(path)
        return {"content": [{"type": "text", "text": f"Saved {path}."}]}

    @tool("replace_in_file", "Replace one unique snippet in an editable Python source file. Prefer this for large files; tests are read-only.",
          {"path": str, "old": str, "new": str})
    async def replace_in_file(args):
        path = args["path"]
        error = _replace_source_snippet(workspace, editable, path, args["old"], args["new"])
        if error is not None:
            return {"content": [{"type": "text", "text": error}], "is_error": True}
        touched.add(path)
        return {"content": [{"type": "text", "text": f"Replaced one snippet in {path}."}]}

    server = create_sdk_mcp_server(name="repository", tools=[list_files, read_file, write_file, replace_in_file])
    options = ClaudeAgentOptions(
        tools=[], mcp_servers={"repository": server},
        allowed_tools=["mcp__repository__list_files", "mcp__repository__read_file",
                       "mcp__repository__write_file", "mcp__repository__replace_in_file"],
        strict_mcp_config=True, setting_sources=[], cwd=str(workspace), max_turns=16,
        env={
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_AUTH_TOKEN": DEEPSEEK_API_KEY,
            "ANTHROPIC_API_KEY": "",
            "ANTHROPIC_MODEL": DEEPSEEK_MODEL,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": DEEPSEEK_MODEL,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": DEEPSEEK_MODEL,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": DEEPSEEK_MODEL,
            "CLAUDE_CODE_SUBAGENT_MODEL": DEEPSEEK_MODEL,
            "ENABLE_TOOL_SEARCH": "false",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "3072",
            "CLAUDE_CODE_MAX_RETRIES": "1",
            "API_TIMEOUT_MS": "60000",
            "CLAUDE_CONFIG_DIR": str(workspace / ".claude-config"),
        }, stderr=lambda _line: None,
    )
    try:
        async for message in query(
            prompt=("Repair the reported bug in this Python Git repository. Use only the listed MCP tools. "
                    "You may edit multiple listed source files, but never edit tests. Do not run commands or use other services. "
                    "First list files and read the relevant sources and any selected test, then make the smallest complete fix. "
                    "For large source files, replace a short unique snippet rather than rewriting the whole file. "
                    f"User issue: {prompt}\nValidation evidence or previous failure:\n{failure[:5000]}"),
            options=options,
        ):
            if cancel_check is not None and cancel_check():
                raise AgentExecutionError("cancelled", "The task was cancelled.")
            if isinstance(message, ResultMessage):
                final = message
    except ResultError as exc:
        if exc.terminal_reason == "max_turns" or exc.subtype == "error_max_turns":
            raise AgentExecutionError("agent_max_turns", "The Agent reached its turn limit.") from None
        detail = f"HTTP {exc.api_error_status}" if exc.api_error_status else (exc.terminal_reason or exc.subtype or "unknown")
        raise AgentExecutionError("agent_execution_failed", f"The Agent run failed ({detail}).") from None
    if not touched or final is None or final.is_error or final.subtype != "success":
        raise AgentExecutionError("agent_incomplete", "The Agent did not complete a source code repair.")
    return final.num_turns


def run_repository_repair(task_id: str, name: str, base_commit: str, test_path: str | None, prompt: str,
                          *, validation_mode: str = "pytest", test_paths: list[str] | None = None,
                          run_path: str | None = None, run_args: list[str] | None = None,
                          expected_output: str | None = None, cancel_check=None, progress=None) -> RepairResult:
    if not DEEPSEEK_API_KEY:
        raise AgentExecutionError("api_key_missing", "The Worker has no DeepSeek API key configured.")
    if DEEPSEEK_MODEL not in {"deepseek-flash", "deepseek-flash[1m]"}:
        raise AgentExecutionError("unsupported_model", "This project is configured for DeepSeek Flash only.")
    try:
        repo = repo_path(name)
        if git(repo, "rev-parse", "HEAD") != base_commit:
            raise AgentExecutionError("repo_changed", "The repository moved after the task was submitted.")
    except RepositoryError as exc:
        raise AgentExecutionError(exc.code, exc.public_message) from None
    runtime = ROOT / ".runtime" / "jobs"
    runtime.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"{task_id}-", dir=runtime) as temp:
        workspace = Path(temp)
        if TEST_EXECUTION_MODE in {"docker", "remote"}:
            workspace.chmod(0o755)
        original = _snapshot(repo, base_commit, workspace)
        editable = {p: content for p, content in original.items() if _editable(p, content)}
        if not editable:
            raise AgentExecutionError("no_source", "The repository has no supported Python source files.")
        paths = test_paths or ([test_path] if test_path else [])
        if validation_mode == "pytest":
            if not paths or any(path not in original or not (workspace / path).is_file() for path in paths):
                raise AgentExecutionError("test_missing", "The selected tracked pytest files are missing.")
        elif validation_mode == "run":
            if run_path not in editable:
                raise AgentExecutionError("run_path_missing", "The selected Python entrypoint is not editable.")
        elif validation_mode != "manual":
            raise AgentExecutionError("invalid_validation_mode", "The validation mode is not supported.")
        if cancel_check is not None and cancel_check():
            raise AgentExecutionError("cancelled", "The task was cancelled.")
        baseline_output = None
        if validation_mode in {"pytest", "run"}:
            try:
                baseline = (run_fixture_tests(workspace, paths) if validation_mode == "pytest"
                            else run_python_script(workspace, run_path, run_args))
            except SandboxError as exc:
                raise AgentExecutionError(exc.code, exc.public_message) from None
            baseline_output = _visible_output(baseline, BASELINE_OUTPUT_LIMIT)
            if validation_mode == "pytest" and baseline.returncode == 0:
                raise AgentExecutionError("baseline_passed", "The selected tests already pass; provide tests that reproduce the bug.",
                                          baseline_output, baseline_output=baseline_output)
            if validation_mode == "run" and expected_output and baseline.returncode == 0 and expected_output in baseline.stdout:
                raise AgentExecutionError("baseline_passed", "The program already produces the expected output.",
                                          baseline_output, baseline_output=baseline_output)
            if progress is not None:
                progress("baseline_failed" if baseline.returncode else "baseline_checked",
                         "已运行修复前检查。" if validation_mode == "run" else "已确认所选测试能复现问题。")
            failure_context = (
                f"Before repair: exit code {baseline.returncode}.\n{baseline_output}"
                + (f"\nExpected stdout to contain: {expected_output}" if expected_output else "")
            )
        else:
            failure_context = "No executable checker was selected. Repair the reported issue and keep valid Python syntax."

        total_turns = 0
        last_patch = ""
        last_output = ""
        for attempt in range(1, MAX_REPAIR_PASSES + 1):
            if cancel_check is not None and cancel_check():
                raise AgentExecutionError("cancelled", "The task was cancelled.")
            remaining = REPOSITORY_TASK_SECONDS - (time.perf_counter() - started)
            if remaining <= POST_VALIDATION_RESERVE_SECONDS + 5:
                raise AgentExecutionError(
                    "validation_failed" if last_patch else "agent_timeout",
                    "The repair did not pass validation within the task time limit.",
                    last_output or None, diff=last_patch or None, baseline_output=baseline_output,
                )
            if progress is not None:
                progress("agent_running", "Agent 正在分析并修复代码。" if attempt == 1 else "Agent 根据验证失败结果再次修复。")
            try:
                total_turns += asyncio.run(asyncio.wait_for(
                    _run_repo_agent(workspace, prompt, failure_context, editable,
                                    paths if validation_mode == "pytest" else run_path, cancel_check),
                    timeout=remaining - POST_VALIDATION_RESERVE_SECONDS,
                ))
            except AgentExecutionError as exc:
                if last_patch and exc.code != "cancelled":
                    raise AgentExecutionError(
                        "validation_failed",
                        f"The second repair pass did not complete ({exc.code}). The previous candidate is available for review.",
                        exc.test_output or last_output, diff=last_patch,
                        baseline_output=baseline_output,
                    ) from exc
                raise
            except Exception as exc:
                code = "validation_failed" if last_patch else (
                    "agent_timeout" if isinstance(exc, TimeoutError) else "agent_execution_failed"
                )
                message = ("The second repair pass did not complete; the previous candidate is available for review."
                           if last_patch else
                           "The Agent run timed out." if code == "agent_timeout"
                           else f"Agent failed ({type(exc).__name__}).")
                raise AgentExecutionError(
                    code, message, last_output or None, diff=last_patch or None,
                    baseline_output=baseline_output,
                ) from None

            changed = {p: (workspace / p).read_bytes() for p in editable if (workspace / p).read_bytes() != original[p]}
            if not changed:
                raise AgentExecutionError(
                    "validation_failed" if last_patch else "empty_repair",
                    "The second repair pass left no candidate; the previous candidate is available for review."
                    if last_patch else "The Agent did not change any source files.",
                    last_output or None, diff=last_patch or None, baseline_output=baseline_output,
                )
            patch_parts = []
            for path, after_bytes in sorted(changed.items()):
                before_text = original[path].decode("utf-8")
                after_text = after_bytes.decode("utf-8")
                patch_parts.extend(difflib.unified_diff(
                    before_text.splitlines(keepends=True), after_text.splitlines(keepends=True),
                    fromfile=f"a/{path}", tofile=f"b/{path}",
                ))
            patch = "".join(patch_parts)
            if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
                raise AgentExecutionError(
                    "validation_failed" if last_patch else "patch_too_large",
                    "The second repair pass exceeds the patch limit; the previous candidate is available for review."
                    if last_patch else "The repair exceeds the review patch limit.",
                    last_output or None, diff=last_patch or None, baseline_output=baseline_output,
                )
            last_patch = patch
            if progress is not None:
                progress("agent_modified", f"Agent 已修改 {len(changed)} 个源文件，开始验证。")
            if cancel_check is not None and cancel_check():
                raise AgentExecutionError("cancelled", "The task was cancelled.")

            syntax_error = None
            for path, content in changed.items():
                try:
                    compile(content.decode("utf-8"), path, "exec")
                except (SyntaxError, UnicodeError, ValueError) as exc:
                    syntax_error = f"{path}: {exc}"
                    break
            if syntax_error:
                passed = False
                post_output = f"Python syntax check failed: {syntax_error}"
                status_code = "syntax_failed"
            elif validation_mode == "manual":
                passed = True
                post_output = "Modified Python files passed syntax checks. No runtime check was selected."
                status_code = ""
            else:
                try:
                    checked = (run_fixture_tests(workspace, paths) if validation_mode == "pytest"
                               else run_python_script(workspace, run_path, run_args))
                except SandboxError as exc:
                    raise AgentExecutionError(exc.code, exc.public_message, last_output or None, diff=patch,
                                              baseline_output=baseline_output) from None
                post_output = _visible_output(checked)
                passed = checked.returncode == 0 and (
                    validation_mode == "pytest" or not expected_output or expected_output in checked.stdout
                )
                status_code = "tests_failed" if validation_mode == "pytest" else "run_failed"
                post_output = f"Exit code: {checked.returncode}\n{post_output}"
                if validation_mode == "run" and expected_output and expected_output not in checked.stdout:
                    post_output += f"\nExpected stdout to contain: {expected_output}"

            last_output = _validation_output(baseline_output, post_output, attempt)
            if passed:
                verified = validation_mode == "pytest" or bool(expected_output)
                if progress is not None:
                    progress("tests_passed" if verified else "validation_unverified",
                             "所选验证已通过。" if verified else "程序已运行或语法已检查，等待人工确认。")
                return RepairResult(
                    summary=(f"Changed {len(changed)} Python source files; "
                             + ("selected validation passed." if verified else "manual verification required.")),
                    diff=patch, test_output=last_output,
                    duration_ms=int((time.perf_counter() - started) * 1000), agent_turns=total_turns,
                    validation_status="passed" if verified else "unverified",
                    baseline_output=baseline_output,
                )
            if progress is not None:
                progress("validation_failed", f"第 {attempt} 次修改未通过验证。")
            failure_context = f"Previous repair failed validation.\n{post_output[:5000]}"
            if attempt == MAX_REPAIR_PASSES:
                raise AgentExecutionError(
                    status_code, "The repaired repository did not pass validation.",
                    last_output, diff=patch, baseline_output=baseline_output,
                )
        raise AssertionError("unreachable repair loop")
