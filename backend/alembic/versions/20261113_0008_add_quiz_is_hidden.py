"""add is_hidden and hidden_reason to quizzes table (FR-ADM-03)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quizzes",
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "quizzes",
        sa.Column("hidden_reason", sa.Text(), nullable=True),
    )
    op.create_index(op.f("ix_quizzes_is_hidden"), "quizzes", ["is_hidden"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quizzes_is_hidden"), table_name="quizzes")
    op.drop_column("quizzes", "hidden_reason")
    op.drop_column("quizzes", "is_hidden")
