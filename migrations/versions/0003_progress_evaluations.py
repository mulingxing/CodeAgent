"""progress events and evaluation runs

Revision ID: 0003_progress_evaluations
Revises: 0002_create_outbox
Create Date: 2026-11-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_progress_evaluations"
down_revision: Union[str, None] = "0002_create_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("eval_run_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("phase", sa.String(length=40), nullable=False, server_default="queued"))
        batch_op.create_foreign_key("fk_tasks_eval_run_id", "eval_runs", ["eval_run_id"], ["id"])
        batch_op.create_index("ix_tasks_eval_run_id", ["eval_run_id"])
    op.execute(sa.text("UPDATE tasks SET phase = status WHERE status IN ('succeeded', 'failed', 'cancelled')"))
    op.execute(sa.text("UPDATE tasks SET phase = 'starting' WHERE status = 'running'"))
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_events_task_id", table_name="task_events")
    op.drop_table("task_events")
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_index("ix_tasks_eval_run_id")
        batch_op.drop_constraint("fk_tasks_eval_run_id", type_="foreignkey")
        batch_op.drop_column("phase")
        batch_op.drop_column("eval_run_id")
    op.drop_table("eval_runs")
