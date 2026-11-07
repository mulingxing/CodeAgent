from __future__ import annotations

import ast
import asyncio
import difflib
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from codeagent.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, ROOT, TEST_EXECUTION_MODE
from codeagent.benchmarks import CASE_BY_ID, render_case
from codeagent.sandbox import SandboxError, run_fixture_tests

FIXTURE = ROOT / "fixtures"
MAX_OUTPUT = 12_000


class AgentExecutionError(Exception):
    def __init__(self, code: str, message: str, test_output: str | None = None,
                 *, diff: str | None = None, baseline_output: str | None = None):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.test_output = test_output
        self.diff = diff
        self.baseline_output = baseline_output


@dataclass(frozen=True)
class RepairResult:
    summary: str
    diff: str
    test_output: str
    duration_ms: int
    agent_turns: int
    validation_status: str | None = None
    baseline_output: str | None = None


def safe_candidate(text: str) -> bool:
    """Only permit the known, side-effect-free function in this stage's demo repo."""
    if len(text.encode("utf-8")) > 4096 or not text.isascii() or "#" in text:
        return False
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return False
    fn = tree.body[0]
    expected = ast.parse("def is_adult(age):\n    return age >= 18\n").body[0]
    return (
        fn.name == "is_adult"
        and not fn.decorator_list
        and not getattr(fn, "type_params", [])
        and len(fn.args.args) == 1
        and fn.args.args[0].arg == "age"
        and not fn.args.posonlyargs
        and not fn.args.kwonlyargs
        and fn.args.vararg is None
        and fn.args.kwarg is None
        and not fn.args.defaults
        and len(fn.body) == 1
        and ast.dump(fn.body[0], include_attributes=False)
        == ast.dump(expected.body[0], include_attributes=False)
        and (fn.returns is None or isinstance(fn.returns, ast.Name) and fn.returns.id == "bool")
        and (fn.args.args[0].annotation is None or isinstance(fn.args.args[0].annotation, ast.Name) and fn.args.args[0].annotation.id == "int")
    )


def safe_benchmark_candidate(text: str, argument_names: tuple[str, ...]) -> bool:
    """Allow one pure expression over the case arguments, with no calls or imports."""
    if len(text.encode("utf-8")) > 4096 or not text.isascii() or "#" in text:
        return False
    try:
        tree = ast.parse(text)
    except (SyntaxError, RecursionError):
        return False
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return False
    fn = tree.body[0]
    if (
        fn.name != "solve" or fn.decorator_list or fn.returns is not None
        or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.vararg or fn.args.kwarg
        or fn.args.defaults or fn.args.kw_defaults or len(fn.body) != 1
        or not isinstance(fn.body[0], ast.Return)
        or tuple(arg.arg for arg in fn.args.args) != argument_names
        or any(arg.annotation is not None for arg in fn.args.args)
    ):
        return False
    allowed = (
        ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
        ast.Name, ast.Load, ast.Constant, ast.BinOp, ast.Add, ast.Sub,
        ast.Mult, ast.FloorDiv, ast.Mod, ast.UnaryOp, ast.USub, ast.UAdd,
        ast.Not, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Eq,
        ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.IfExp,
    )
    nodes = list(ast.walk(tree))
    return len(nodes) <= 80 and all(
        isinstance(node, allowed)
        and (not isinstance(node, ast.Name) or node.id in argument_names)
        and (not isinstance(node, ast.Constant) or type(node.value) in (int, bool) and abs(int(node.value)) <= 1000)
        for node in nodes
    )


async def _run_agent(workspace: Path, prompt: str, cancel_check=None, *, target_name="age.py", validator=safe_candidate) -> tuple[bool, bool, bool, int]:
    from claude_agent_sdk import ClaudeAgentOptions, ResultError, ResultMessage, create_sdk_mcp_server, query, tool

    target = workspace / target_name
    called = {"read": False, "write": False}
    final_message: ResultMessage | None = None

    @tool("read_code", "Read the single sample Python source file.", {})
    async def read_code(_args):
        called["read"] = True
        return {"content": [{"type": "text", "text": target.read_text(encoding="utf-8")}]}

    @tool("write_code", "Replace the single sample Python source file with corrected code.", {"content": str})
    async def write_code(args):
        content = args["content"]
        if not validator(content):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Rejected. Write one pure function with a single return expression. "
                        "Use only its arguments, numbers, arithmetic, comparisons, Boolean operators, "
                        "or conditional expressions. Function calls (including max), imports, "
                        "comments, and extra definitions are not allowed."
                    ),
                }],
                "is_error": True,
            }
        target.write_text(content, encoding="utf-8")
        called["write"] = True
        return {"content": [{"type": "text", "text": "Saved the corrected sample."}]}

    server = create_sdk_mcp_server(name="fixture", tools=[read_code, write_code])
    options = ClaudeAgentOptions(
        tools=[],
        mcp_servers={"fixture": server},
        allowed_tools=["mcp__fixture__read_code", "mcp__fixture__write_code"],
        strict_mcp_config=True,
        setting_sources=[],
        cwd=str(workspace),
        max_turns=8,
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
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2048",
            "CLAUDE_CODE_MAX_RETRIES": "1",
            "API_TIMEOUT_MS": "60000",
            "CLAUDE_CONFIG_DIR": str(workspace / ".claude-config"),
        },
        stderr=lambda _line: None,
    )
    try:
        async for message in query(
            prompt=(
                "Handle this request in the single sample file using only the provided tools. "
                "Never attempt commands, other files, or external services. "
                "Write one pure function with a single return expression; do not call functions "
                "or builtins such as max. Use a conditional expression when needed. "
                f"User request: {prompt}\n"
                "The repository tests define the expected behavior. Write only the single function."
            ),
            options=options,
        ):
            if cancel_check is not None and cancel_check():
                raise AgentExecutionError("cancelled", "The task was cancelled.")
            if isinstance(message, ResultMessage):
                final_message = message
    except ResultError as exc:
        if exc.terminal_reason == "max_turns" or exc.subtype == "error_max_turns":
            raise AgentExecutionError("agent_max_turns", "The Agent reached its turn limit before completing the repair.") from None
        detail = f"HTTP {exc.api_error_status}" if exc.api_error_status is not None else (exc.terminal_reason or exc.subtype or "unknown")
        raise AgentExecutionError("agent_execution_failed", f"The Agent run failed ({detail}).") from None

    if final_message is None:
        return called["read"], called["write"], False, 0
    succeeded = (
        final_message.subtype == "success"
        and not final_message.is_error
        and final_message.terminal_reason in (None, "completed")
        and final_message.stop_reason != "refusal"
        and final_message.api_error_status is None
    )
    return called["read"], called["write"], succeeded, final_message.num_turns


def run_controlled_repair(task_id: str, repo_id: str, prompt: str, *, cancel_check=None, progress=None) -> RepairResult:
    case = CASE_BY_ID.get(repo_id)
    if repo_id != "age-boundary-demo" and case is None:
        raise AgentExecutionError("unsupported_repo", "This stage only supports curated repair cases.")
    if not DEEPSEEK_API_KEY:
        raise AgentExecutionError("api_key_missing", "The Worker has no DeepSeek API key configured.")
    if DEEPSEEK_MODEL not in {"deepseek-flash", "deepseek-flash[1m]"}:
        raise AgentExecutionError("unsupported_model", "This stage is configured for DeepSeek Flash only.")

    runtime = ROOT / ".runtime" / "jobs"
    runtime.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"{task_id}-", dir=runtime) as temp:
        workspace = Path(temp)
        if TEST_EXECUTION_MODE in ("docker", "remote"):
            workspace.chmod(0o755)
        if cancel_check is not None and cancel_check():
            raise AgentExecutionError("cancelled", "The task was cancelled.")
        if case is None:
            target_name = "age.py"
            for name in ("age.py", "test_age.py"):
                shutil.copy2(FIXTURE / name, workspace / name)
            validator = safe_candidate
        else:
            target_name = "solution.py"
            source, tests = render_case(case)
            (workspace / target_name).write_text(source, encoding="utf-8")
            (workspace / "test_solution.py").write_text(tests, encoding="utf-8")
            validator = lambda text: safe_benchmark_candidate(text, case.args)

        try:
            baseline = run_fixture_tests(workspace)
        except SandboxError as exc:
            raise AgentExecutionError(exc.code, exc.public_message) from None
        if baseline.returncode != 1 or "failed" not in baseline.stdout:
            raise AgentExecutionError("fixture_invalid", "The demonstration fixture did not produce its expected baseline failure.")
        if progress is not None:
            progress("baseline_failed", "已确认样例中的 Bug 会使测试失败。")
        original = (workspace / target_name).read_text(encoding="utf-8")

        if cancel_check is not None and cancel_check():
            raise AgentExecutionError("cancelled", "The task was cancelled.")

        if progress is not None:
            progress("agent_running", "Agent 正在分析并修改代码。")

        try:
            read, write, agent_succeeded, turns = asyncio.run(
                asyncio.wait_for(
                    _run_agent(workspace, (case.description + " " + prompt) if case else prompt, cancel_check,
                               target_name=target_name, validator=validator), timeout=180
                )
            )
        except AgentExecutionError:
            raise
        except Exception as exc:
            code = "agent_timeout" if isinstance(exc, TimeoutError) else "agent_execution_failed"
            message = "The Agent run exceeded its time limit." if code == "agent_timeout" else f"The Agent run failed ({type(exc).__name__})."
            raise AgentExecutionError(code, message) from None

        if not (read and write and agent_succeeded):
            raise AgentExecutionError("agent_incomplete", "The Agent did not complete the required read and write steps.")
        if progress is not None:
            progress("agent_modified", "Agent 已提交修改，开始校验和测试。")
        fixed = (workspace / target_name).read_text(encoding="utf-8")
        if not validator(fixed):
            raise AgentExecutionError("unsafe_output", "The Agent output failed the code safety check.")

        if cancel_check is not None and cancel_check():
            raise AgentExecutionError("cancelled", "The task was cancelled.")
        try:
            after = run_fixture_tests(workspace)
        except SandboxError as exc:
            raise AgentExecutionError(exc.code, exc.public_message) from None
        if cancel_check is not None and cancel_check():
            raise AgentExecutionError("cancelled", "The task was cancelled.")
        if after.returncode != 0:
            raise AgentExecutionError("tests_failed", "The repaired sample did not pass its test suite.")
        if progress is not None:
            progress("tests_passed", "修复后的固定测试已通过。")
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(True), fixed.splitlines(True),
                fromfile=f"before/{target_name}", tofile=f"after/{target_name}",
            )
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        return RepairResult(
            summary=f"The Agent repaired {repo_id} and all fixture tests passed.",
            diff=diff[:MAX_OUTPUT],
            test_output=(baseline.stdout + "\n--- after repair ---\n" + after.stdout)[:MAX_OUTPUT],
            duration_ms=duration_ms,
            agent_turns=turns,
        )
