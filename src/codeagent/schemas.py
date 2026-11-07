from __future__ import annotations

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from codeagent.benchmarks import CASE_BY_ID
from codeagent.models import TaskStatus


class TaskCreate(BaseModel):
    repo_id: str = Field(default="age-boundary-demo", max_length=80)
    prompt: str = Field(min_length=8, max_length=2000)

    @field_validator("repo_id")
    @classmethod
    def known_case(cls, value: str) -> str:
        if value != "age-boundary-demo" and value not in CASE_BY_ID:
            raise ValueError("unknown curated case")
        return value

    @field_validator("prompt")
    @classmethod
    def prompt_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("prompt must contain at least 8 non-whitespace characters")
        return value


class RepositoryTaskCreate(BaseModel):
    repo_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")
    validation_mode: Literal["pytest", "run", "manual"] = "pytest"
    test_path: str | None = Field(default=None, max_length=240, pattern=r"^tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_-]+\.py$")
    test_paths: list[str] | None = None
    run_path: str | None = Field(default=None, max_length=240, pattern=r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py$")
    run_args: list[str] = Field(default_factory=list, max_length=8)
    expected_output: str | None = Field(default=None, max_length=300)
    prompt: str = Field(min_length=8, max_length=2000)

    @field_validator("test_paths")
    @classmethod
    def valid_test_paths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        import re
        if not 1 <= len(value) <= 8 or len(set(value)) != len(value) or any(
            len(path) > 240 or re.fullmatch(r"tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_-]+\.py", path) is None
            for path in value
        ):
            raise ValueError("select one to eight distinct tracked pytest files")
        return value

    @field_validator("run_args")
    @classmethod
    def valid_run_args(cls, value: list[str]) -> list[str]:
        if any(len(arg) > 100 or "\x00" in arg or "\n" in arg or "\r" in arg for arg in value):
            raise ValueError("run arguments must be short single-line values")
        return value

    @model_validator(mode="after")
    def valid_mode_fields(self):
        if self.validation_mode == "pytest":
            if not (self.test_paths or self.test_path):
                raise ValueError("select at least one pytest file")
            if self.test_path and self.test_paths and self.test_path != self.test_paths[0]:
                raise ValueError("test_path must match the first selected test")
        elif self.validation_mode == "run":
            if not self.run_path:
                raise ValueError("select a tracked Python entrypoint")
            if self.test_path or self.test_paths:
                raise ValueError("pytest files are not used in run mode")
            if self.expected_output is not None and not self.expected_output.strip():
                self.expected_output = None
        elif self.test_path or self.test_paths or self.run_path or self.run_args or self.expected_output:
            raise ValueError("manual mode does not run a test or program")
        return self

    @field_validator("prompt")
    @classmethod
    def prompt_must_contain_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("prompt must contain at least 8 non-whitespace characters")
        return value


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repo_id: str
    source_repo: str | None
    base_commit: str | None
    base_branch: str | None
    test_path: str | None
    test_paths: list[str] | None
    validation_mode: str
    validation_status: str | None
    run_path: str | None
    run_args: list[str] | None
    expected_output: str | None
    baseline_output: str | None
    eval_run_id: str | None
    prompt: str
    status: TaskStatus
    phase: str
    celery_task_id: str | None
    attempts: int
    duration_ms: int | None
    agent_turns: int | None
    result_summary: str | None
    diff: str | None
    test_output: str | None
    error_code: str | None
    error_message: str | None
    review_branch: str | None
    review_commit: str | None
    review_path: str | None
    review_status: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class TaskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    phase: str
    message: str
    created_at: datetime


class EvalCaseRead(BaseModel):
    task_id: str
    case_id: str
    title: str
    status: TaskStatus
    phase: str
    duration_ms: int | None
    error_code: str | None


class EvaluationRead(BaseModel):
    id: str
    created_at: datetime
    total: int
    finished: int
    succeeded: int
    failed: int
    cancelled: int
    success_rate: float
    agent_completion_rate: float
    test_pass_rate: float
    avg_duration_ms: float | None
    avg_agent_turns: float | None
    cases: list[EvalCaseRead]
