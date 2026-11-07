from codeagent.tasks import execute_code_task


def enqueue_code_task(task_id: str) -> str:
    return execute_code_task.delay(task_id).id
