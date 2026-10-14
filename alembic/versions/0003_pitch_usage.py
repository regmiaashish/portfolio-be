"""Add pitch_usage: one row per UTC day counting paid Pitch Me runs (daily cap).

Revision ID: 0003_pitch_usage
Revises: 0002_contact_messages
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_pitch_usage"
down_revision: str | Sequence[str] | None = "0002_contact_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pitch_usage",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("runs", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pitch_usage_pkey")),
    )


def downgrade() -> None:
    op.drop_table("pitch_usage")
