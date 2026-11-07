from celery import Celery

from codeagent.config import CELERY_BROKER_URL

celery_app = Celery(
    "codeagent",
    broker=CELERY_BROKER_URL,
    include=["codeagent.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Repository repairs have their own 300-second budget. These limits only
    # catch a Worker that cannot exit after that budget (including cleanup).
    task_time_limit=420,
    task_soft_time_limit=390,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=3,
    broker_transport_options={"visibility_timeout": 600, "socket_connect_timeout": 3, "socket_timeout": 3},
    task_publish_retry=False,
    enable_utc=True,
    timezone="UTC",
    beat_schedule={
        "flush-task-outbox": {
            "task": "codeagent.flush_outbox",
            "schedule": 15.0,
        },
    },
)
