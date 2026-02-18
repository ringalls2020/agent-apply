"""application attempt indexes

Revision ID: 20260217_0005
Revises: 20260217_0004
Create Date: 2026-02-17 13:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_0005"
down_revision = "20260217_0004"
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
    if _has_table("application_attempts"):
        if not _has_index(
            "application_attempts", "ix_application_attempts_user_created_at"
        ):
            op.create_index(
                "ix_application_attempts_user_created_at",
                "application_attempts",
                ["user_id", "created_at"],
                unique=False,
            )
        if not _has_index(
            "application_attempts", "ix_application_attempts_user_external_run_id"
        ):
            op.create_index(
                "ix_application_attempts_user_external_run_id",
                "application_attempts",
                ["user_id", "external_run_id"],
                unique=False,
            )


def downgrade() -> None:
    if _has_table("application_attempts"):
        if _has_index(
            "application_attempts", "ix_application_attempts_user_external_run_id"
        ):
            op.drop_index(
                "ix_application_attempts_user_external_run_id",
                table_name="application_attempts",
            )
        if _has_index(
            "application_attempts", "ix_application_attempts_user_created_at"
        ):
            op.drop_index(
                "ix_application_attempts_user_created_at",
                table_name="application_attempts",
            )
