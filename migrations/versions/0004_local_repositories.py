"""local Git repository tasks and review state

Revision ID: 0004_local_repositories
Revises: 0003_progress_evaluations
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_local_repositories"
down_revision: Union[str, None] = "0003_progress_evaluations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("source_repo", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("base_commit", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("test_path", sa.String(length=240), nullable=True))
        batch_op.add_column(sa.Column("review_branch", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("review_path", sa.String(length=240), nullable=True))
        batch_op.add_column(sa.Column("review_status", sa.String(length=30), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("review_status")
        batch_op.drop_column("review_path")
        batch_op.drop_column("review_branch")
        batch_op.drop_column("test_path")
        batch_op.drop_column("base_commit")
        batch_op.drop_column("source_repo")
