"""Add contact_messages: contact-form submissions are stored before sending.

Revision ID: 0002_contact_messages
Revises: 0001_baseline_users
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_contact_messages"
down_revision: str | Sequence[str] | None = "0001_baseline_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contact_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            # op.f(): the name is final; the naming convention must not be applied again.
            name=op.f("contact_messages_status_check"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("contact_messages_pkey")),
    )
    op.create_index(op.f("ix_contact_messages_created_at"), "contact_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_messages_created_at"), table_name="contact_messages")
    op.drop_table("contact_messages")
