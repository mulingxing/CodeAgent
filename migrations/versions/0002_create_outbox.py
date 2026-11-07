"""create task outbox

Revision ID: 0002_create_outbox
Revises: 0001_create_tasks
Create Date: 2026-11-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_create_outbox"
down_revision: Union[str, None] = "0001_create_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_outbox",
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_task_outbox_published_at", "task_outbox", ["published_at"], unique=False)
    op.execute(
        sa.text(
            "INSERT INTO task_outbox (task_id, created_at, available_at, published_at) "
            "SELECT id, created_at, created_at, "
            "CASE WHEN status = 'queued' THEN NULL ELSE created_at END FROM tasks"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_task_outbox_published_at", table_name="task_outbox")
    op.drop_table("task_outbox")
