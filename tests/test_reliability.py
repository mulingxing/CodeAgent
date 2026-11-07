from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from codeagent.api import create_app
from codeagent.celery_app import celery_app
from codeagent.database import make_engine, make_session_factory
from codeagent.models import Base, Task, TaskOutbox, TaskStatus
from codeagent.repo_runner import POST_VALIDATION_RESERVE_SECONDS, REPOSITORY_TASK_SECONDS
from codeagent.runner import AgentExecutionError, RepairResult
from codeagent.sandbox_api import app as sandbox_app
from codeagent.tasks import STALE_AFTER, process_task, publish_pending_tasks


def database(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'reliability.db'}")
    Base.metadata.create_all(engine)
    return engine, make_session_factory(engine)


def submit(client):
    return client.post(
        "/api/v1/tasks",
        json={"repo_id": "age-boundary-demo", "prompt": "Fix the age boundary for 18."},
    )


def test_broker_outage_keeps_task_and_outbox_replays_it(tmp_path):
    engine, sessions = database(tmp_path)

    def unavailable(_task_id):
        raise ConnectionError("Redis unavailable")

    with TestClient(create_app(session_factory=sessions, dispatcher=unavailable)) as client:
        response = submit(client)
        assert response.status_code == 202
        task_id = response.json()["id"]
        assert response.json()["status"] == "queued"
        assert response.json()["celery_task_id"] is None

    sent = []
    assert publish_pending_tasks(session_factory=sessions, publisher=lambda item: sent.append(item) or "celery-1") == 1
    assert publish_pending_tasks(session_factory=sessions, publisher=lambda item: "celery-2") == 0
    assert sent == [task_id]
    with sessions() as session:
        assert session.get(Task, task_id).celery_task_id == "celery-1"
        assert session.get(TaskOutbox, task_id).published_at is not None
    engine.dispose()


def test_queued_cancellation_prevents_replay(tmp_path):
    engine, sessions = database(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _item: (_ for _ in ()).throw(ConnectionError()))) as client:
        task_id = submit(client).json()["id"]
        cancelled = client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert client.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 200
    sent = []
    assert publish_pending_tasks(session_factory=sessions, publisher=lambda item: sent.append(item)) == 0
    assert sent == []
    engine.dispose()


def test_running_cancellation_discards_late_result(tmp_path):
    engine, sessions = database(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _item: "celery-1")) as client:
        task_id = submit(client).json()["id"]

        def runner(_task_id, _repo_id, _prompt, *, cancel_check, progress):
            assert client.post(f"/api/v1/tasks/{task_id}/cancel").json()["status"] == "cancelled"
            assert cancel_check()
            return RepairResult("late result", "diff", "3 passed", 1, 1)

        assert process_task(task_id, session_factory=sessions, runner=runner)["status"] == "cancelled"
        fetched = client.get(f"/api/v1/tasks/{task_id}").json()
        assert fetched["status"] == "cancelled"
        assert fetched["result_summary"] is None
    engine.dispose()


def test_agent_timeout_is_rescheduled_with_bounded_attempts(tmp_path):
    engine, sessions = database(tmp_path)
    with TestClient(create_app(session_factory=sessions, dispatcher=lambda _item: "celery-1")) as client:
        task_id = submit(client).json()["id"]

    def timed_out(*_args, **_kwargs):
        raise AgentExecutionError("agent_timeout", "The Agent run exceeded its time limit.")

    assert process_task(task_id, session_factory=sessions, runner=timed_out)["status"] == "queued"
    with sessions() as session:
        task = session.get(Task, task_id)
        outbox = session.get(TaskOutbox, task_id)
        assert task.attempts == 1
        assert task.status == TaskStatus.QUEUED.value
        assert outbox.published_at is None
        ready_at = outbox.available_at.replace(tzinfo=timezone.utc) if outbox.available_at.tzinfo is None else outbox.available_at
        assert ready_at > datetime.now(timezone.utc)
    engine.dispose()


def test_worker_limits_leave_repository_budget_and_do_not_reclaim_live_lease(tmp_path):
    soft_limit = celery_app.conf.task_soft_time_limit
    hard_limit = celery_app.conf.task_time_limit
    assert REPOSITORY_TASK_SECONDS + POST_VALIDATION_RESERVE_SECONDS < soft_limit
    assert soft_limit < hard_limit < STALE_AFTER.total_seconds()
    assert STALE_AFTER.total_seconds() < celery_app.conf.broker_transport_options["visibility_timeout"]

    engine, sessions = database(tmp_path)
    task_id = "lease-budget-test"
    with sessions() as session:
        session.add(Task(
            id=task_id, repo_id="age-boundary-demo", prompt="Fix the age boundary for 18.",
            status=TaskStatus.RUNNING.value, attempts=1,
            started_at=datetime.now(timezone.utc) - timedelta(seconds=hard_limit + 1),
        ))
        session.commit()

    invoked = []

    def runner(*_args, **_kwargs):
        invoked.append(True)
        return RepairResult("fixed", "diff", "1 passed", 1, 1)

    assert process_task(task_id, session_factory=sessions, runner=runner)["status"] == "running"
    assert invoked == []
    with sessions() as session:
        task = session.get(Task, task_id)
        assert task.attempts == 1
        task.started_at = datetime.now(timezone.utc) - STALE_AFTER - timedelta(seconds=1)
        session.commit()

    assert process_task(task_id, session_factory=sessions, runner=runner)["status"] == "succeeded"
    assert invoked == [True]
    with sessions() as session:
        assert session.get(Task, task_id).attempts == 2
    engine.dispose()


def test_sandbox_service_rejects_paths_outside_fixed_workspace():
    with TestClient(sandbox_app) as client:
        assert client.post("/run-tests", json={"workspace_name": "../../etc"}).status_code == 422
        assert client.post("/run-tests", json={"workspace_name": "nonexistent-workspace-123"}).status_code == 404
