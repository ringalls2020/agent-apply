"""user profile autosubmit and review status

Revision ID: 20260217_0003
Revises: 20260217_0002
Create Date: 2026-02-17 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_0003"
down_revision = "20260217_0002"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    columns = _inspector().get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    if not _has_table("user_application_profiles"):
        op.create_table(
            "user_application_profiles",
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column(
                "autosubmit_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("phone", sa.String(length=64), nullable=True),
            sa.Column("city", sa.String(length=128), nullable=True),
            sa.Column("state", sa.String(length=128), nullable=True),
            sa.Column("country", sa.String(length=128), nullable=True),
            sa.Column("linkedin_url", sa.Text(), nullable=True),
            sa.Column("github_url", sa.Text(), nullable=True),
            sa.Column("portfolio_url", sa.Text(), nullable=True),
            sa.Column("work_authorization", sa.String(length=128), nullable=True),
            sa.Column("requires_sponsorship", sa.Boolean(), nullable=True),
            sa.Column("willing_to_relocate", sa.Boolean(), nullable=True),
            sa.Column("years_experience", sa.Integer(), nullable=True),
            sa.Column("writing_voice", sa.String(length=64), nullable=True),
            sa.Column("cover_letter_style", sa.String(length=64), nullable=True),
            sa.Column("achievements_summary", sa.Text(), nullable=True),
            sa.Column(
                "custom_answers_json",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column("additional_context", sa.Text(), nullable=True),
            sa.Column("gender_encrypted", sa.Text(), nullable=True),
            sa.Column("race_ethnicity_encrypted", sa.Text(), nullable=True),
            sa.Column("veteran_status_encrypted", sa.Text(), nullable=True),
            sa.Column("disability_status_encrypted", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if (
        _has_table("applications")
        and _has_column("applications", "status")
        and _has_column("applications", "user_id")
    ):
        op.execute(
            sa.text(
                "UPDATE applications "
                "SET status = 'review' "
                "WHERE user_id IS NOT NULL AND status = 'discovered'"
            )
        )


def downgrade() -> None:
    if (
        _has_table("applications")
        and _has_column("applications", "status")
        and _has_column("applications", "user_id")
    ):
        op.execute(
            sa.text(
                "UPDATE applications "
                "SET status = 'discovered' "
                "WHERE user_id IS NOT NULL AND status = 'review'"
            )
        )

    if _has_table("user_application_profiles"):
        op.drop_table("user_application_profiles")

