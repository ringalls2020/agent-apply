"""application search indexes

Revision ID: 20260217_0004
Revises: 20260217_0003
Create Date: 2026-02-17 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_0004"
down_revision = "20260217_0003"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    indexes = _inspector().get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    if _has_table("applications"):
        if not _has_index("applications", "ix_applications_user_status"):
            op.create_index(
                "ix_applications_user_status",
                "applications",
                ["user_id", "status"],
                unique=False,
            )
        if not _has_index("applications", "ix_applications_user_company"):
            op.create_index(
                "ix_applications_user_company",
                "applications",
                ["user_id", "opportunity_company"],
                unique=False,
            )


def downgrade() -> None:
    if _has_table("applications"):
        if _has_index("applications", "ix_applications_user_company"):
            op.drop_index("ix_applications_user_company", table_name="applications")
        if _has_index("applications", "ix_applications_user_status"):
            op.drop_index("ix_applications_user_status", table_name="applications")
