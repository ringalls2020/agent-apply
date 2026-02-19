"""widen applications opportunity_id to 128 chars

Revision ID: 20260219_0007
Revises: 20260218_0006
Create Date: 2026-02-19 00:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260219_0007"
down_revision = "20260218_0006"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _column_length(table_name: str, column_name: str) -> int | None:
    if not _has_table(table_name):
        return None
    for column in _inspector().get_columns(table_name):
        if column["name"] != column_name:
            continue
        return getattr(column.get("type"), "length", None)
    return None


def upgrade() -> None:
    current_length = _column_length("applications", "opportunity_id")
    if not isinstance(current_length, int) or current_length >= 128:
        return

    with op.batch_alter_table("applications") as batch_op:
        batch_op.alter_column(
            "opportunity_id",
            existing_type=sa.String(length=current_length),
            type_=sa.String(length=128),
            existing_nullable=False,
        )


def downgrade() -> None:
    current_length = _column_length("applications", "opportunity_id")
    if not isinstance(current_length, int) or current_length <= 36:
        return

    with op.batch_alter_table("applications") as batch_op:
        batch_op.alter_column(
            "opportunity_id",
            existing_type=sa.String(length=current_length),
            type_=sa.String(length=36),
            existing_nullable=False,
        )
