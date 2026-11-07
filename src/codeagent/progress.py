from sqlalchemy.orm import Session

from codeagent.models import Task, TaskEvent


def record_event(session: Session, task: Task, phase: str, message: str) -> None:
    task.phase = phase
    session.add(TaskEvent(task_id=task.id, phase=phase, message=message[:300]))
