from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_repo: Mapped[str | None] = mapped_column(String(80), nullable=True)
    base_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    test_path: Mapped[str | None] = mapped_column(String(240), nullable=True)
    test_paths: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    validation_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="pytest", server_default="pytest")
    validation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    run_path: Mapped[str | None] = mapped_column(String(240), nullable=True)
    run_args: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expected_output: Mapped[str | None] = mapped_column(String(300), nullable=True)
    baseline_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("eval_runs.id"), nullable=True, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskStatus.QUEUED.value, index=True)
    phase: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    celery_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    review_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    review_path: Mapped[str | None] = mapped_column(String(240), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskOutbox(Base):
    __tablename__ = "task_outbox"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
