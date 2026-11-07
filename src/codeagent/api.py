from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from codeagent.database import SessionLocal
from codeagent.benchmarks import CASES
from codeagent.evaluations import evaluation_report
from codeagent.models import EvalRun, Task, TaskEvent, TaskOutbox, TaskStatus
from codeagent.progress import record_event
from codeagent.queue import enqueue_code_task
from codeagent.schemas import EvaluationRead, RepositoryTaskCreate, TaskCreate, TaskRead
from codeagent.git_repos import RepositoryError, create_review, inspect_repo, list_repos, merge_review, validate_submission

Dispatcher = Callable[[str], str]


def create_app(
    *,
    session_factory: sessionmaker[Session] = SessionLocal,
    dispatcher: Dispatcher = enqueue_code_task,
) -> FastAPI:
    app = FastAPI(title="CodeAgent Task API", version="0.4.0")

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/styles.css", include_in_schema=False)
    def dashboard_styles() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "styles.css", media_type="text/css")

    @app.get("/health")
    def health() -> dict[str, str]:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/api/v1/cases")
    def list_cases() -> list[dict[str, str]]:
        return [{"case_id": case.case_id, "title": case.title, "description": case.description} for case in CASES]

    @app.get("/api/v1/repositories")
    def repositories() -> list[dict]:
        return list_repos()

    @app.post("/api/v1/repository-tasks", response_model=TaskRead, status_code=status.HTTP_202_ACCEPTED)
    def create_repository_task(request: RepositoryTaskCreate, response: Response) -> TaskRead:
        try:
            info = validate_submission(
                request.repo_name, request.test_path,
                validation_mode=request.validation_mode,
                test_paths=request.test_paths, run_path=request.run_path,
            )
        except RepositoryError as exc:
            raise HTTPException(status_code=409, detail=exc.public_message) from None
        task_id = str(uuid4())
        with session_factory() as session:
            paths = request.test_paths or ([request.test_path] if request.test_path else None)
            task = Task(id=task_id, repo_id=request.repo_name, source_repo=request.repo_name,
                        base_commit=info["commit"], base_branch=info["branch"],
                        test_path=paths[0] if paths else None, test_paths=paths,
                        validation_mode=request.validation_mode,
                        run_path=request.run_path if request.validation_mode == "run" else None,
                        run_args=request.run_args if request.validation_mode == "run" else None,
                        expected_output=request.expected_output if request.validation_mode == "run" else None,
                        prompt=request.prompt, status=TaskStatus.QUEUED.value)
            session.add(task)
            session.add(TaskOutbox(task_id=task_id))
            record_event(session, task, "queued", "本地仓库任务已创建，等待 Worker。")
            session.commit()
        try:
            celery_id = dispatcher(task_id)
        except Exception:
            celery_id = None
        with session_factory() as session:
            task = session.get(Task, task_id)
            if celery_id is not None:
                task.celery_task_id = celery_id
                session.get(TaskOutbox, task_id).published_at = datetime.now(timezone.utc)
                record_event(session, task, "queued", "任务已投递到 Worker 队列。")
                session.commit()
            response.headers["Location"] = f"/api/v1/tasks/{task_id}"
            return TaskRead.model_validate(task)

    @app.post("/api/v1/tasks/{task_id}/review", response_model=TaskRead)
    def prepare_review(task_id: str, action: str | None = Header(default=None, alias="X-CodeAgent-Action")) -> TaskRead:
        if action != "review":
            raise HTTPException(status_code=403, detail="Use the dashboard review action.")
        with session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            if task.source_repo is None or task.status != TaskStatus.SUCCEEDED.value or not task.diff:
                raise HTTPException(status_code=409, detail="Only a successful repository task can be reviewed.")
            if task.review_status:
                raise HTTPException(status_code=409, detail="Review was already prepared.")
            try:
                branch, path, commit = create_review(task.source_repo, task.id, task.base_commit, task.base_branch, task.diff)
            except RepositoryError as exc:
                raise HTTPException(status_code=409, detail=exc.public_message) from None
            task.review_branch = branch
            task.review_commit = commit
            task.review_path = path
            task.review_status = "ready"
            record_event(session, task, "review_ready", "审查分支和工作树已创建，请检查后再合并。")
            session.commit()
            return TaskRead.model_validate(task)

    @app.post("/api/v1/tasks/{task_id}/merge", response_model=TaskRead)
    def merge_repository_task(task_id: str, action: str | None = Header(default=None, alias="X-CodeAgent-Action")) -> TaskRead:
        if action != "merge":
            raise HTTPException(status_code=403, detail="Use the dashboard merge action.")
        with session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            if task.review_status != "ready" or not task.source_repo or not task.review_branch or not task.review_path or not task.review_commit:
                raise HTTPException(status_code=409, detail="Prepare and inspect the review worktree first.")
            try:
                cleaned = merge_review(task.source_repo, task.base_commit, task.base_branch, task.review_branch, task.review_commit, task.review_path)
            except RepositoryError as exc:
                raise HTTPException(status_code=409, detail=exc.public_message) from None
            task.review_status = "merged" if cleaned else "merged_needs_cleanup"
            record_event(session, task, "merged", "修改已合并到原仓库，审查工作树已清理。" if cleaned else "修改已合并；审查工作树需要手动清理。")
            session.commit()
            return TaskRead.model_validate(task)

    @app.get("/api/v1/tasks", response_model=list[TaskRead])
    def list_tasks(limit: int = Query(default=20, ge=1, le=100)) -> list[TaskRead]:
        with session_factory() as session:
            tasks = session.scalars(select(Task).order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)).all()
            return [TaskRead.model_validate(task) for task in tasks]

    @app.post("/api/v1/tasks", response_model=TaskRead, status_code=status.HTTP_202_ACCEPTED)
    def create_task(request: TaskCreate, response: Response) -> TaskRead:
        task_id = str(uuid4())
        with session_factory() as session:
            task = Task(
                id=task_id,
                repo_id=request.repo_id,
                prompt=request.prompt.strip(),
                status=TaskStatus.QUEUED.value,
            )
            session.add(task)
            session.add(TaskOutbox(task_id=task_id))
            record_event(session, task, "queued", "任务已创建，等待投递。")
            session.commit()

        try:
            celery_id = dispatcher(task_id)
        except Exception:
            celery_id = None

        with session_factory() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise HTTPException(status_code=500, detail="Task record disappeared after enqueue.")
            if celery_id is not None:
                task.celery_task_id = celery_id
                outbox = session.get(TaskOutbox, task_id)
                outbox.published_at = datetime.now(timezone.utc)
                record_event(session, task, "queued", "任务已投递到 Worker 队列。")
                session.commit()
            session.refresh(task)
            response.headers["Location"] = f"/api/v1/tasks/{task_id}"
            return TaskRead.model_validate(task)

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskRead)
    def get_task(task_id: str) -> TaskRead:
        with session_factory() as session:
            task = session.scalar(select(Task).where(Task.id == task_id))
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            return TaskRead.model_validate(task)

    @app.get("/api/v1/tasks/{task_id}/events")
    async def stream_task_events(
        task_id: str,
        follow: bool = True,
        since: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        with session_factory() as session:
            if session.get(Task, task_id) is None:
                raise HTTPException(status_code=404, detail="Task not found.")
        cursor = since
        if last_event_id and last_event_id.isdecimal():
            cursor = max(cursor, int(last_event_id))

        async def events():
            nonlocal cursor
            heartbeat = 0
            while True:
                with session_factory() as session:
                    rows = session.scalars(
                        select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.id > cursor)
                        .order_by(TaskEvent.id).limit(100)
                    ).all()
                    task = session.get(Task, task_id)
                    terminal = task is None or task.status in (
                        TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value
                    )
                    payloads = [
                        (row.id, json.dumps({"id": row.id, "phase": row.phase, "message": row.message,
                                             "created_at": row.created_at.isoformat()}, ensure_ascii=False))
                        for row in rows
                    ]
                for event_id, data in payloads:
                    cursor = event_id
                    yield f"id: {event_id}\nevent: progress\ndata: {data}\n\n"
                if len(payloads) == 100:
                    continue
                if not follow or terminal:
                    break
                heartbeat += 1
                if heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat = 0
                await asyncio.sleep(1)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskRead)
    def cancel_task(task_id: str) -> TaskRead:
        with session_factory() as session:
            changed = session.execute(
                update(Task)
                .where(Task.id == task_id, Task.status.in_([TaskStatus.QUEUED.value, TaskStatus.RUNNING.value]))
                .values(status=TaskStatus.CANCELLED.value, completed_at=datetime.now(timezone.utc))
            )
            task = session.get(Task, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Task not found.")
            if changed.rowcount == 0 and task.status not in (TaskStatus.CANCELLED.value,):
                raise HTTPException(status_code=409, detail="Task has already finished.")
            if changed.rowcount == 1:
                record_event(session, task, "cancelled", "任务已取消。")
            session.commit()
            session.refresh(task)
            return TaskRead.model_validate(task)

    @app.post("/api/v1/evaluations", response_model=EvaluationRead, status_code=status.HTTP_202_ACCEPTED)
    def create_evaluation(response: Response) -> EvaluationRead:
        run_id = str(uuid4())
        with session_factory() as session:
            run = EvalRun(id=run_id, total_cases=len(CASES))
            session.add(run)
            for case in CASES:
                task = Task(id=str(uuid4()), repo_id=case.case_id, eval_run_id=run_id,
                            prompt=case.description, status=TaskStatus.QUEUED.value)
                session.add(task)
                session.add(TaskOutbox(task_id=task.id))
                record_event(session, task, "queued", "评测任务已创建，等待投递。")
            session.commit()
            report = evaluation_report(session, run)
        response.headers["Location"] = f"/api/v1/evaluations/{run_id}"
        return report

    @app.get("/api/v1/evaluations", response_model=list[EvaluationRead])
    def list_evaluations(limit: int = Query(default=10, ge=1, le=50)) -> list[EvaluationRead]:
        with session_factory() as session:
            runs = session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)).all()
            return [evaluation_report(session, run) for run in runs]

    @app.get("/api/v1/evaluations/{run_id}", response_model=EvaluationRead)
    def get_evaluation(run_id: str) -> EvaluationRead:
        with session_factory() as session:
            run = session.get(EvalRun, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Evaluation not found.")
            return evaluation_report(session, run)

    return app
