"""Add the site content tables edited from the admin: profile, projects, career, skills.

Revision ID: 0004_site_content
Revises: 0003_pitch_usage
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_site_content"
down_revision: str | Sequence[str] | None = "0003_pitch_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "site_profile",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("location", sa.String(length=60), nullable=False),
        sa.Column("tagline", sa.String(length=160), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone_display", sa.String(length=40), nullable=True),
        sa.Column("phone_href", sa.String(length=60), nullable=True),
        sa.Column(
            "socials", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("site_profile_single_row_check")),
        sa.PrimaryKeyConstraint("id", name=op.f("site_profile_pkey")),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column(
            "stack", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("repo_url", sa.String(length=300), nullable=True),
        sa.Column("demo_url", sa.String(length=300), nullable=True),
        sa.Column("image_key", sa.String(length=300), nullable=True),
        sa.Column("image_alt", sa.String(length=200), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=True),
        sa.Column("image_height", sa.Integer(), nullable=True),
        sa.Column("case_study", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("published", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("projects_pkey")),
        sa.UniqueConstraint("slug", name=op.f("projects_slug_key")),
    )
    op.create_table(
        "career_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=100), nullable=False),
        sa.Column("company", sa.String(length=100), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.String(length=600), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("career_entries_pkey")),
    )
    op.create_table(
        "skill_groups",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column(
            "skills", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("skill_groups_pkey")),
    )


def downgrade() -> None:
    op.drop_table("skill_groups")
    op.drop_table("career_entries")
    op.drop_table("projects")
    op.drop_table("site_profile")
