"""Add site_profile.resume_url: a résumé link or uploaded PDF shown on the public site.

Revision ID: 0005_profile_resume
Revises: 0004_site_content
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_profile_resume"
down_revision: str | Sequence[str] | None = "0004_site_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("site_profile", sa.Column("resume_url", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("site_profile", "resume_url")
