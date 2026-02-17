"""auth and user scoped applications

Revision ID: 20260217_0002
Revises: 20260217_0001
Create Date: 2026-02-17 00:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_0002"
down_revision = "20260217_0001"
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


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    indexes = _inspector().get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    if _has_table("users"):
        if not _has_column("users", "password_salt"):
            op.add_column("users", sa.Column("password_salt", sa.String(length=255), nullable=True))
        if not _has_column("users", "password_hash"):
            op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))

    if _has_table("applications"):
        if not _has_column("applications", "user_id"):
            op.add_column(
                "applications",
                sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True),
            )
        if not _has_index("applications", "ix_applications_user_discovered_at"):
            op.create_index(
                "ix_applications_user_discovered_at",
                "applications",
                ["user_id", "opportunity_discovered_at"],
                unique=False,
            )


def downgrade() -> None:
    if _has_table("applications"):
        if _has_index("applications", "ix_applications_user_discovered_at"):
            op.drop_index("ix_applications_user_discovered_at", table_name="applications")
        if _has_column("applications", "user_id"):
            op.drop_column("applications", "user_id")

    if _has_table("users"):
        if _has_column("users", "password_hash"):
            op.drop_column("users", "password_hash")
        if _has_column("users", "password_salt"):
            op.drop_column("users", "password_salt")
