from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from codeagent import repo_runner


def test_agent_can_patch_a_large_source_file_without_rewriting_it(tmp_path: Path, monkeypatch) -> None:
    source_path = tmp_path / "src" / "planner.py"
    source_path.parent.mkdir()
    original = ("# retained context\n" * 1000) + "def travel_days():\n    return 1\n"
    source_path.write_text(original, encoding="utf-8")
    selected_test = tmp_path / "tests" / "test_planner.py"
    selected_test.parent.mkdir()
    selected_test.write_text("def test_days():\n    assert True\n", encoding="utf-8")

    class ResultMessage:
        subtype = "success"
        is_error = False
        num_turns = 3

    class Options:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def tool(name, _description, _schema):
        def decorate(function):
            function.tool_name = name
            return function
        return decorate

    def server(*, name, tools):
        assert name == "repository"
        return {item.tool_name: item for item in tools}

    async def query(*, prompt, options):
        assert "replace a short unique snippet" in prompt
        assert "mcp__repository__replace_in_file" in options.allowed_tools
        tools = options.mcp_servers["repository"]
        listed = await tools["list_files"]({})
        assert "src/planner.py" in listed["content"][0]["text"]
        denied = await tools["replace_in_file"]({
            "path": "tests/test_planner.py", "old": "assert True", "new": "assert False",
        })
        assert denied["is_error"] is True
        saved = await tools["replace_in_file"]({
            "path": "src/planner.py", "old": "    return 1\n", "new": "    return 2\n",
        })
        assert "Replaced one snippet" in saved["content"][0]["text"]
        yield ResultMessage()

    fake_sdk = SimpleNamespace(
        ClaudeAgentOptions=Options, ResultError=Exception, ResultMessage=ResultMessage,
        create_sdk_mcp_server=server, query=query, tool=tool,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    turns = asyncio.run(repo_runner._run_repo_agent(
        tmp_path, "Fix the date span", "five failures",
        {"src/planner.py": original.encode("utf-8")}, "tests/test_planner.py",
    ))
    assert turns == 3
    assert source_path.read_text(encoding="utf-8") == original.replace("    return 1\n", "    return 2\n")
    assert selected_test.read_text(encoding="utf-8") == "def test_days():\n    assert True\n"


def test_snippet_edit_rejects_ambiguous_or_invalid_edits_and_preserves_crlf(tmp_path: Path) -> None:
    path = "src/compute.py"
    source_path = tmp_path / path
    source_path.parent.mkdir()
    original = b"def first():\r\n    return 1\r\n\r\ndef second():\r\n    return 1\r\n"
    source_path.write_bytes(original)
    editable = {path: original}

    ambiguous = repo_runner._replace_source_snippet(
        tmp_path, editable, path, "    return 1\n", "    return 2\n",
    )
    assert "not unique" in ambiguous
    assert source_path.read_bytes() == original

    invalid = repo_runner._replace_source_snippet(
        tmp_path, editable, path,
        "def second():\n    return 1\n", "def second():\n    return (\n",
    )
    assert "syntax is invalid" in invalid
    assert source_path.read_bytes() == original

    saved = repo_runner._replace_source_snippet(
        tmp_path, editable, path,
        "def second():\n    return 1\n", "def second():\n    return 2\n",
    )
    assert saved is None
    assert source_path.read_bytes() == original.replace(
        b"def second():\r\n    return 1\r\n",
        b"def second():\r\n    return 2\r\n",
    )
