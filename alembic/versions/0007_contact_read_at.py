"""Add contact_messages.read_at so the admin can tell read messages from unread ones.

NULL means unread; existing rows stay unread. No IP address or other visitor data is added.

Revision ID: 0007_contact_read_at
Revises: 0006_pitch_runs
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_contact_read_at"
down_revision: str | Sequence[str] | None = "0006_pitch_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contact_messages", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Serves the unread count and the "unread" filter (WHERE read_at IS NULL).
    op.create_index(op.f("ix_contact_messages_read_at"), "contact_messages", ["read_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_contact_messages_read_at"), table_name="contact_messages")
    op.drop_column("contact_messages", "read_at")
