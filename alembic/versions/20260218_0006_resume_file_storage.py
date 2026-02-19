"""resume file storage columns

Revision ID: 20260218_0006
Revises: 20260217_0005
Create Date: 2026-02-18 16:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260218_0006"
down_revision = "20260217_0005"
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
    if not _has_table("resumes"):
        return
    if not _has_column("resumes", "file_bytes"):
        op.add_column("resumes", sa.Column("file_bytes", sa.LargeBinary(), nullable=True))
    if not _has_column("resumes", "file_mime_type"):
        op.add_column("resumes", sa.Column("file_mime_type", sa.String(length=255), nullable=True))
    if not _has_column("resumes", "file_size_bytes"):
        op.add_column("resumes", sa.Column("file_size_bytes", sa.Integer(), nullable=True))
    if not _has_column("resumes", "file_sha256"):
        op.add_column("resumes", sa.Column("file_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    if not _has_table("resumes"):
        return
    with op.batch_alter_table("resumes") as batch_op:
        if _has_column("resumes", "file_sha256"):
            batch_op.drop_column("file_sha256")
        if _has_column("resumes", "file_size_bytes"):
            batch_op.drop_column("file_size_bytes")
        if _has_column("resumes", "file_mime_type"):
            batch_op.drop_column("file_mime_type")
        if _has_column("resumes", "file_bytes"):
            batch_op.drop_column("file_bytes")
