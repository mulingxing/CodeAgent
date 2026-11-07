"""Store repository validation modes and evidence.

Revision ID: 0006_repository_validation
Revises: 0005_review_commit
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_repository_validation"
down_revision: Union[str, None] = "0005_review_commit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("test_paths", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("validation_mode", sa.String(length=20), nullable=False, server_default="pytest"))
        batch_op.add_column(sa.Column("validation_status", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("run_path", sa.String(length=240), nullable=True))
        batch_op.add_column(sa.Column("run_args", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("expected_output", sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column("baseline_output", sa.Text(), nullable=True))
    op.execute(
        "UPDATE tasks SET validation_status = 'passed' "
        "WHERE source_repo IS NOT NULL AND status = 'succeeded'"
    )
    op.execute(
        "UPDATE tasks SET validation_status = 'failed' "
        "WHERE source_repo IS NOT NULL AND status = 'failed' "
        "AND error_code IN ('tests_failed', 'run_failed', 'syntax_failed', 'validation_failed')"
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("baseline_output")
        batch_op.drop_column("expected_output")
        batch_op.drop_column("run_args")
        batch_op.drop_column("run_path")
        batch_op.drop_column("validation_status")
        batch_op.drop_column("validation_mode")
        batch_op.drop_column("test_paths")
