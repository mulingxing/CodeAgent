from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import sessionmaker

from codeagent.celery_app import celery_app
from codeagent.database import SessionLocal
from codeagent.models import Task, TaskOutbox, TaskStatus
from codeagent.progress import record_event
from codeagent.runner import AgentExecutionError, RepairResult, run_controlled_repair
from codeagent.repo_runner import run_repository_repair

Runner = Callable[..., RepairResult]
MAX_ATTEMPTS = 3
# Reclaim only after Celery's 420-second hard limit has stopped the old Worker.
STALE_AFTER = timedelta(seconds=450)


def process_task(
    task_id: str,
    *,
    session_factory: sessionmaker = SessionLocal,
    runner: Runner = run_controlled_repair,
    repository_runner: Runner = run_repository_repair,
) -> dict[str, str]:
    """Claim once, ignore duplicate delivery, and reclaim a stale Worker lease."""
    now = datetime.now(timezone.utc)
    stale_before = now - STALE_AFTER
    with session_factory() as session:
        current = session.get(Task, task_id)
        if current is None:
            return {"task_id": task_id, "status": "missing"}
        started_at = current.started_at
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if (
            current.status == TaskStatus.RUNNING.value
            and started_at is not None
            and started_at <= stale_before
            and current.attempts >= MAX_ATTEMPTS
        ):
            current.status = TaskStatus.FAILED.value
            current.error_code = "worker_lost"
            current.error_message = "The Worker stopped during execution too many times."
            current.completed_at = now
            record_event(session, current, "failed", "Worker 多次中断，任务已失败。")
            session.commit()
            return {"task_id": task_id, "status": TaskStatus.FAILED.value}

        claimed = session.execute(
            update(Task)
            .where(
                Task.id == task_id,
                or_(
                    Task.status == TaskStatus.QUEUED.value,
                    and_(
                        Task.status == TaskStatus.RUNNING.value,
                        Task.started_at <= stale_before,
                        Task.attempts < MAX_ATTEMPTS,
                    ),
                ),
            )
            .values(
                status=TaskStatus.RUNNING.value,
                phase="starting",
                attempts=Task.attempts + 1,
                started_at=now,
                error_code=None,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            session.expire_all()
            current = session.get(Task, task_id)
            return {"task_id": task_id, "status": current.status if current else "missing"}
        session.expire_all()
        task = session.get(Task, task_id)
        record_event(session, task, "starting", "Worker 已领取任务。")
        session.commit()
        repo_id = task.repo_id
        prompt = task.prompt
        source_repo = task.source_repo
        base_commit = task.base_commit
        test_path = task.test_path
        test_paths = task.test_paths
        validation_mode = task.validation_mode
        run_path = task.run_path
        run_args = task.run_args
        expected_output = task.expected_output

    def cancellation_requested() -> bool:
        with session_factory() as check_session:
            current_task = check_session.get(Task, task_id)
            return current_task is None or current_task.status == TaskStatus.CANCELLED.value

    def report_progress(phase: str, message: str) -> None:
        with session_factory() as progress_session:
            current_task = progress_session.get(Task, task_id)
            if current_task is not None and current_task.status == TaskStatus.RUNNING.value:
                record_event(progress_session, current_task, phase, message)
                progress_session.commit()

    try:
        if cancellation_requested():
            return {"task_id": task_id, "status": TaskStatus.CANCELLED.value}
        if source_repo is not None:
            result = repository_runner(task_id, source_repo, base_commit, test_path, prompt,
                                       validation_mode=validation_mode, test_paths=test_paths,
                                       run_path=run_path, run_args=run_args,
                                       expected_output=expected_output,
                                       cancel_check=cancellation_requested, progress=report_progress)
        else:
            result = runner(task_id, repo_id, prompt, cancel_check=cancellation_requested, progress=report_progress)
    except AgentExecutionError as exc:
        with session_factory() as session:
            task = session.get(Task, task_id)
            if task is not None and task.status == TaskStatus.RUNNING.value:
                if exc.code == "agent_timeout" and task.attempts < MAX_ATTEMPTS:
                    task.status = TaskStatus.QUEUED.value
                    task.started_at = None
                    task.error_code = exc.code
                    task.error_message = "The Agent timed out; a retry is scheduled."
                    outbox = session.get(TaskOutbox, task_id)
                    if outbox is not None:
                        outbox.published_at = None
                        outbox.available_at = datetime.now(timezone.utc) + timedelta(seconds=5 * 2 ** task.attempts)
                    record_event(session, task, "retry_wait", "Agent 超时，等待下一次尝试。")
                else:
                    task.status = TaskStatus.CANCELLED.value if exc.code == "cancelled" else TaskStatus.FAILED.value
                if exc.code != "cancelled" and task.status == TaskStatus.FAILED.value:
                    task.error_code = exc.code
                    task.error_message = exc.public_message
                    task.test_output = exc.test_output
                    task.diff = exc.diff
                    task.baseline_output = exc.baseline_output
                    if source_repo is not None and exc.code in {
                        "tests_failed", "run_failed", "syntax_failed", "validation_failed"
                    }:
                        task.validation_status = "failed"
                if task.status != TaskStatus.QUEUED.value:
                    task.completed_at = datetime.now(timezone.utc)
                    if task.status == TaskStatus.FAILED.value:
                        record_event(session, task, "failed", f"任务失败：{exc.code}。")
                    elif task.status == TaskStatus.CANCELLED.value:
                        record_event(session, task, "cancelled", "任务已取消。")
                session.commit()
            return {"task_id": task_id, "status": task.status if task else "missing"}
    except Exception:
        with session_factory() as session:
            task = session.get(Task, task_id)
            if task is not None and task.status == TaskStatus.RUNNING.value:
                task.status = TaskStatus.FAILED.value
                task.error_code = "worker_error"
                task.error_message = "The Worker encountered an unexpected error."
                task.completed_at = datetime.now(timezone.utc)
                record_event(session, task, "failed", "Worker 执行失败。")
                session.commit()
            return {"task_id": task_id, "status": task.status if task else "missing"}

    with session_factory() as session:
        task = session.get(Task, task_id)
        if task is None:
            return {"task_id": task_id, "status": "missing"}
        if task.status == TaskStatus.RUNNING.value:
            task.status = TaskStatus.SUCCEEDED.value
            task.result_summary = result.summary
            task.diff = result.diff
            task.test_output = result.test_output
            task.baseline_output = result.baseline_output
            task.validation_status = result.validation_status or ("passed" if source_repo else None)
            task.duration_ms = result.duration_ms
            task.agent_turns = result.agent_turns
            task.completed_at = datetime.now(timezone.utc)
            if source_repo is None:
                message = "修复完成，固定测试全部通过。"
            elif task.validation_status == "passed":
                message = "修复完成，所选验证已通过。"
            else:
                message = "候选修复已生成，等待人工验证。"
            record_event(session, task, "succeeded", message)
            session.commit()
        return {"task_id": task_id, "status": task.status}


@celery_app.task(name="codeagent.execute_task")
def execute_code_task(task_id: str) -> dict[str, str]:
    result = process_task(task_id)
    if result["status"] == TaskStatus.RUNNING.value:
        # A late-acked message can be requeued immediately after a Worker crash.
        # Defer its retry until the previous execution lease has expired.
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            started_at = task.started_at if task is not None else None
        if started_at is not None:
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            seconds_left = (started_at + STALE_AFTER - datetime.now(timezone.utc)).total_seconds()
            if seconds_left > 0:
                execute_code_task.apply_async(args=[task_id], countdown=int(seconds_left) + 2)
    return result


def publish_pending_tasks(*, session_factory=SessionLocal, publisher=None, limit: int = 100) -> int:
    """Replay durable task submissions after an API crash or broker outage."""
    if publisher is None:
        publisher = lambda task_id: execute_code_task.delay(task_id).id
    with session_factory() as session:
        pending_ids = session.scalars(
            select(TaskOutbox.task_id)
            .where(TaskOutbox.published_at.is_(None), TaskOutbox.available_at <= datetime.now(timezone.utc))
            .order_by(TaskOutbox.created_at)
            .limit(limit)
        ).all()

    published = 0
    for task_id in pending_ids:
        with session_factory() as session:
            task = session.get(Task, task_id)
            outbox = session.get(TaskOutbox, task_id)
            if outbox is None or outbox.published_at is not None:
                continue
            if task is None or task.status != TaskStatus.QUEUED.value:
                outbox.published_at = datetime.now(timezone.utc)
                session.commit()
                continue

        try:
            celery_id = publisher(task_id)
        except Exception:
            break
        with session_factory() as session:
            outbox = session.get(TaskOutbox, task_id)
            task = session.get(Task, task_id)
            if outbox is not None and outbox.published_at is None:
                outbox.published_at = datetime.now(timezone.utc)
            if task is not None and task.celery_task_id is None:
                task.celery_task_id = celery_id
            if task is not None and task.status == TaskStatus.QUEUED.value:
                record_event(session, task, "queued", "任务已投递到 Worker 队列。")
            session.commit()
        published += 1
    return published


@celery_app.task(name="codeagent.flush_outbox")
def flush_outbox() -> int:
    return publish_pending_tasks()
