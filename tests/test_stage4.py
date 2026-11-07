import asyncio

import pytest
from claude_agent_sdk import ResultError
from fastapi.testclient import TestClient

from codeagent.api import create_app
from codeagent.benchmarks import CASES, render_case
from codeagent.database import make_engine, make_session_factory
from codeagent.models import Base
from codeagent.runner import AgentExecutionError, RepairResult, _run_agent, safe_benchmark_candidate
from codeagent.sandbox import run_fixture_tests
from codeagent.tasks import process_task


FIXES = {
    "adult_boundary": "age >= 18",
    "even_number": "n % 2 == 0",
    "pass_score": "score >= 60",
    "free_shipping": "total >= 99",
    "maximum_two": "a if a >= b else b",
    "minimum_two": "a if a <= b else b",
    "absolute_value": "n if n >= 0 else -n",
    "nonnegative": "n if n >= 0 else 0",
    "multiple_of_three": "n % 3 == 0",
    "both_divisible": "n % 3 == 0 and n % 5 == 0",
    "square": "n * n",
    "remaining_stock": "stock - sold if stock >= sold else 0",
}


def test_all_curated_cases_fail_before_and_pass_after_reference_fix(tmp_path):
    assert len(CASES) == 12
    for case in CASES:
        workspace = tmp_path / case.case_id
        workspace.mkdir()
        source, tests = render_case(case)
        (workspace / "solution.py").write_text(source, encoding="utf-8")
        (workspace / "test_solution.py").write_text(tests, encoding="utf-8")
        assert run_fixture_tests(workspace).returncode == 1, case.case_id
        code = f"def solve({', '.join(case.args)}):\n    return {FIXES[case.case_id]}\n"
        assert safe_benchmark_candidate(code, case.args), case.case_id
        (workspace / "solution.py").write_text(code, encoding="utf-8")
        assert run_fixture_tests(workspace).returncode == 0, case.case_id


def test_benchmark_guard_rejects_file_or_network_access():
    assert not safe_benchmark_candidate("import os\ndef solve(n):\n    return n\n", ("n",))
    assert not safe_benchmark_candidate("def solve(n):\n    return open('/etc/passwd').read()\n", ("n",))


def test_agent_turn_limit_is_reported_explicitly(monkeypatch, tmp_path):
    import claude_agent_sdk

    async def failed_query(*_args, **_kwargs):
        raise ResultError(
            "Agent reached its turn limit.",
            data={"subtype": "error_max_turns", "terminal_reason": "max_turns"},
            exit_code=1,
        )
        yield

    monkeypatch.setattr(claude_agent_sdk, "query", failed_query)
    (tmp_path / "solution.py").write_text("def solve(stock, sold):\n    return stock - sold\n", encoding="utf-8")
    with pytest.raises(AgentExecutionError) as error:
        asyncio.run(_run_agent(tmp_path, "Fix the remaining stock calculation.", target_name="solution.py"))
    assert error.value.code == "agent_max_turns"


def test_evaluation_report_and_replayable_events(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'stage4.db'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    app = create_app(session_factory=sessions, dispatcher=lambda _task_id: "queued-1")
    with TestClient(app) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "进入工作台" in dashboard.text
        assert "代码修复工作台" in dashboard.text
        styles = client.get("/styles.css")
        assert styles.status_code == 200
        assert "text/css" in styles.headers["content-type"]
        created = client.post("/api/v1/evaluations")
        assert created.status_code == 202
        report = created.json()
        assert report["total"] == 12
        assert report["finished"] == 0
        assert len(report["cases"]) == 12
        assert len(client.get("/api/v1/cases").json()) == 12

        task_id = report["cases"][0]["task_id"]
        events = client.get(f"/api/v1/tasks/{task_id}/events?follow=false")
        assert events.status_code == 200
        assert "event: progress" in events.text
        assert "任务已创建" in events.text or "评测任务已创建" in events.text
        first_event_id = int(events.text.split("id: ", 1)[1].split("\n", 1)[0])

        def runner(_task_id, _repo_id, _prompt, *, cancel_check, progress):
            progress("agent_running", "Agent 正在分析。")
            progress("agent_modified", "Agent 已修改代码。")
            progress("tests_passed", "测试通过。")
            return RepairResult("fixed", "diff", "3 passed", 100, 2)

        assert process_task(task_id, session_factory=sessions, runner=runner)["status"] == "succeeded"
        updated = client.get(f"/api/v1/evaluations/{report['id']}").json()
        assert updated["finished"] == 1
        assert updated["succeeded"] == 1
        assert updated["success_rate"] == 8.3
        assert updated["agent_completion_rate"] == 8.3
        assert updated["test_pass_rate"] == 8.3
        assert updated["avg_duration_ms"] == 100
        assert updated["avg_agent_turns"] == 2
        later = client.get(
            f"/api/v1/tasks/{task_id}/events?follow=false",
            headers={"Last-Event-ID": str(first_event_id)},
        ).text
        assert "succeeded" in later
        assert f"id: {first_event_id}\n" not in later
    engine.dispose()
