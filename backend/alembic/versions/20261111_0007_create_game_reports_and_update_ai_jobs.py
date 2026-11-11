"""create game_reports table and add session_id to ai_jobs (FR-AIRPT-01..06)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Thêm session_id và ràng buộc duy nhất (session_id, kind) cho bảng ai_jobs
    op.add_column("ai_jobs", sa.Column("session_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_ai_jobs_session_id",
        "ai_jobs",
        "game_sessions",
        ["session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_ai_jobs_session_id", "ai_jobs", ["session_id"])
    op.create_unique_constraint(
        "uq_ai_jobs_session_kind",
        "ai_jobs",
        ["session_id", "kind"],
    )

    # 2. Tạo bảng game_reports
    op.create_table(
        "game_reports",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'ready'"),
            nullable=False,
        ),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "strengths",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "weaknesses",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("advice_for_host", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("advice_for_learner", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'degraded', 'failed')",
            name="ck_game_reports_status",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name="fk_game_reports_session_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_game_reports"),
        sa.UniqueConstraint("session_id", name="uq_game_reports_session_id"),
    )
    op.create_index("ix_game_reports_session_id", "game_reports", ["session_id"])
    op.create_index("ix_game_reports_status", "game_reports", ["status"])


def downgrade() -> None:
    op.drop_index("ix_game_reports_status", table_name="game_reports")
    op.drop_index("ix_game_reports_session_id", table_name="game_reports")
    op.drop_table("game_reports")
    op.drop_constraint("uq_ai_jobs_session_kind", "ai_jobs", type_="unique")
    op.drop_index("ix_ai_jobs_session_id", table_name="ai_jobs")
    op.drop_constraint("fk_ai_jobs_session_id", "ai_jobs", type_="foreignkey")
    op.drop_column("ai_jobs", "session_id")
