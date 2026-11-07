from sqlalchemy import select
from sqlalchemy.orm import Session

from codeagent.benchmarks import CASE_BY_ID
from codeagent.models import EvalRun, Task, TaskEvent, TaskStatus
from codeagent.schemas import EvalCaseRead, EvaluationRead


def evaluation_report(session: Session, run: EvalRun) -> EvaluationRead:
    tasks = session.scalars(
        select(Task).where(Task.eval_run_id == run.id).order_by(Task.created_at, Task.id)
    ).all()
    succeeded = sum(task.status == TaskStatus.SUCCEEDED.value for task in tasks)
    failed = sum(task.status == TaskStatus.FAILED.value for task in tasks)
    cancelled = sum(task.status == TaskStatus.CANCELLED.value for task in tasks)
    durations = [task.duration_ms for task in tasks if task.status == TaskStatus.SUCCEEDED.value and task.duration_ms is not None]
    turns = [task.agent_turns for task in tasks if task.status == TaskStatus.SUCCEEDED.value and task.agent_turns is not None]
    progress_events = session.execute(
        select(TaskEvent.task_id, TaskEvent.phase)
        .where(TaskEvent.task_id.in_([task.id for task in tasks]), TaskEvent.phase.in_(["agent_modified", "tests_passed"]))
    ).all() if tasks else []
    agent_completed = {task_id for task_id, phase in progress_events if phase == "agent_modified"}
    tests_passed = {task_id for task_id, phase in progress_events if phase == "tests_passed"}
    return EvaluationRead(
        id=run.id,
        created_at=run.created_at,
        total=run.total_cases,
        finished=succeeded + failed + cancelled,
        succeeded=succeeded,
        failed=failed,
        cancelled=cancelled,
        success_rate=round(100 * succeeded / run.total_cases, 1) if run.total_cases else 0.0,
        agent_completion_rate=round(100 * len(agent_completed) / run.total_cases, 1) if run.total_cases else 0.0,
        test_pass_rate=round(100 * len(tests_passed) / run.total_cases, 1) if run.total_cases else 0.0,
        avg_duration_ms=round(sum(durations) / len(durations), 1) if durations else None,
        avg_agent_turns=round(sum(turns) / len(turns), 1) if turns else None,
        cases=[
            EvalCaseRead(
                task_id=task.id,
                case_id=task.repo_id,
                title=CASE_BY_ID[task.repo_id].title,
                status=TaskStatus(task.status),
                phase=task.phase,
                duration_ms=task.duration_ms,
                error_code=task.error_code,
            )
            for task in tasks
        ],
    )
