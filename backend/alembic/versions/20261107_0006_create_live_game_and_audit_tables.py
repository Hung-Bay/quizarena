"""create live game and audit tables (game_sessions, game_players, player_answers, audit_logs)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Bảng game_sessions
    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("quiz_id", sa.Uuid(), nullable=False),
        sa.Column("quiz_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("host_id", sa.Uuid(), nullable=False),
        sa.Column("pin", sa.String(length=6), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'lobby'"),
            nullable=False,
        ),
        sa.Column(
            "strategy_id",
            sa.String(length=32),
            server_default=sa.text("'standard'"),
            nullable=False,
        ),
        sa.Column("quiz_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('lobby', 'question_active', 'question_result', 'finished', 'cancelled')",
            name=op.f("ck_game_sessions_ck_game_sessions_status"),
        ),
        sa.ForeignKeyConstraint(
            ["host_id"],
            ["users.id"],
            name=op.f("fk_game_sessions_host_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["quiz_id"],
            ["quizzes.id"],
            name=op.f("fk_game_sessions_quiz_id_quizzes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_sessions")),
    )
    op.create_index(op.f("ix_game_sessions_pin"), "game_sessions", ["pin"], unique=False)
    op.create_index(op.f("ix_game_sessions_host_id"), "game_sessions", ["host_id"], unique=False)
    op.create_index(op.f("ix_game_sessions_quiz_id"), "game_sessions", ["quiz_id"], unique=False)
    op.create_index(op.f("ix_game_sessions_status"), "game_sessions", ["status"], unique=False)
    op.create_index(
        op.f("ix_game_sessions_created_at"), "game_sessions", ["created_at"], unique=False
    )

    # 2. Bảng game_players
    op.create_table(
        "game_players",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("guest_id", sa.String(length=64), nullable=True),
        sa.Column("nickname", sa.String(length=50), nullable=False),
        sa.Column("final_score", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("final_rank", sa.Integer(), nullable=True),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(nickname) >= 2 AND length(nickname) <= 50",
            name=op.f("ck_game_players_ck_game_players_nickname_length"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_game_players_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_game_players_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_players")),
        sa.UniqueConstraint("session_id", "nickname", name=op.f("uq_game_players_session_id")),
    )
    op.create_index(
        op.f("ix_game_players_session_id"), "game_players", ["session_id"], unique=False
    )
    op.create_index(op.f("ix_game_players_user_id"), "game_players", ["user_id"], unique=False)

    # 3. Bảng player_answers
    op.create_table(
        "player_answers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("option_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("response_ms", sa.Integer(), nullable=False),
        sa.Column("points_awarded", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["game_players.id"],
            name=op.f("fk_player_answers_player_id_game_players"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["game_sessions.id"],
            name=op.f("fk_player_answers_session_id_game_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_answers")),
        sa.UniqueConstraint(
            "session_id",
            "player_id",
            "question_id",
            name=op.f("uq_player_answers_session_id"),
        ),
    )
    op.create_index(
        op.f("ix_player_answers_session_id"), "player_answers", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_player_answers_player_id"), "player_answers", ["player_id"], unique=False
    )
    op.create_index(
        op.f("ix_player_answers_question_id"), "player_answers", ["question_id"], unique=False
    )
    op.create_index(
        op.f("ix_player_answers_created_at"), "player_answers", ["created_at"], unique=False
    )

    # 4. Bảng audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_actor_id"), "audit_logs", ["actor_id"], unique=False)
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"], unique=False)
    op.create_index(
        "ix_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
        unique=False,
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    # 4. Drop audit_logs
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_id"), table_name="audit_logs")
    op.drop_table("audit_logs")

    # 3. Drop player_answers
    op.drop_index(op.f("ix_player_answers_created_at"), table_name="player_answers")
    op.drop_index(op.f("ix_player_answers_question_id"), table_name="player_answers")
    op.drop_index(op.f("ix_player_answers_player_id"), table_name="player_answers")
    op.drop_index(op.f("ix_player_answers_session_id"), table_name="player_answers")
    op.drop_table("player_answers")

    # 2. Drop game_players
    op.drop_index(op.f("ix_game_players_user_id"), table_name="game_players")
    op.drop_index(op.f("ix_game_players_session_id"), table_name="game_players")
    op.drop_table("game_players")

    # 1. Drop game_sessions
    op.drop_index(op.f("ix_game_sessions_created_at"), table_name="game_sessions")
    op.drop_index(op.f("ix_game_sessions_status"), table_name="game_sessions")
    op.drop_index(op.f("ix_game_sessions_quiz_id"), table_name="game_sessions")
    op.drop_index(op.f("ix_game_sessions_host_id"), table_name="game_sessions")
    op.drop_index(op.f("ix_game_sessions_pin"), table_name="game_sessions")
    op.drop_table("game_sessions")
