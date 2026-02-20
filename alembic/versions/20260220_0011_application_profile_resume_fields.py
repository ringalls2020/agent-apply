"""application profile resume-derived field expansion

Revision ID: 20260220_0011
Revises: 20260219_0010
Create Date: 2026-02-20 09:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260220_0011"
down_revision = "20260219_0010"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        column.get("name") == column_name
        for column in _inspector().get_columns(table_name)
    )


def upgrade() -> None:
    if not _has_table("user_application_profiles"):
        return

    with op.batch_alter_table("user_application_profiles") as batch_op:
        if not _has_column("user_application_profiles", "current_company"):
            batch_op.add_column(sa.Column("current_company", sa.String(length=255), nullable=True))
        if not _has_column("user_application_profiles", "most_recent_company"):
            batch_op.add_column(
                sa.Column("most_recent_company", sa.String(length=255), nullable=True)
            )
        if not _has_column("user_application_profiles", "current_title"):
            batch_op.add_column(sa.Column("current_title", sa.String(length=255), nullable=True))
        if not _has_column("user_application_profiles", "target_work_city"):
            batch_op.add_column(sa.Column("target_work_city", sa.String(length=128), nullable=True))
        if not _has_column("user_application_profiles", "target_work_state"):
            batch_op.add_column(sa.Column("target_work_state", sa.String(length=128), nullable=True))
        if not _has_column("user_application_profiles", "target_work_country"):
            batch_op.add_column(
                sa.Column("target_work_country", sa.String(length=128), nullable=True)
            )


def downgrade() -> None:
    if not _has_table("user_application_profiles"):
        return

    with op.batch_alter_table("user_application_profiles") as batch_op:
        if _has_column("user_application_profiles", "target_work_country"):
            batch_op.drop_column("target_work_country")
        if _has_column("user_application_profiles", "target_work_state"):
            batch_op.drop_column("target_work_state")
        if _has_column("user_application_profiles", "target_work_city"):
            batch_op.drop_column("target_work_city")
        if _has_column("user_application_profiles", "current_title"):
            batch_op.drop_column("current_title")
        if _has_column("user_application_profiles", "most_recent_company"):
            batch_op.drop_column("most_recent_company")
        if _has_column("user_application_profiles", "current_company"):
            batch_op.drop_column("current_company")
