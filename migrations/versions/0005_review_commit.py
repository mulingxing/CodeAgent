"""Record the original branch and immutable review commit.

Revision ID: 0005_review_commit
Revises: 0004_local_repositories
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_review_commit"
down_revision: Union[str, None] = "0004_local_repositories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("base_branch", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("review_commit", sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("review_commit")
        batch_op.drop_column("base_branch")
