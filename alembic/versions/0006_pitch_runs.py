"""Add pitch_runs: every Pitch Me run that started streaming, for the admin's Pitch history.

No IP address or other visitor data is stored.

Revision ID: 0006_pitch_runs
Revises: 0005_profile_resume
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_pitch_runs"
down_revision: str | Sequence[str] | None = "0005_profile_resume"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pitch_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("pitch_text", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=20), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("searches", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('completed', 'cached', 'timeout', 'upstream_error', "
            "'internal_error', 'disconnected')",
            name=op.f("pitch_runs_outcome_check"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pitch_runs_pkey")),
    )
    op.create_index(op.f("ix_pitch_runs_created_at"), "pitch_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_pitch_runs_created_at"), table_name="pitch_runs")
    op.drop_table("pitch_runs")
