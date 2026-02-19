"""add applications opportunity location

Revision ID: 20260219_0008
Revises: 20260219_0007
Create Date: 2026-02-19 14:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260219_0008"
down_revision = "20260219_0007"
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


def _can_backfill_from_job_matches() -> bool:
    required_app_columns = {"id", "user_id", "opportunity_id", "opportunity_location"}
    required_match_columns = {"user_id", "external_job_id", "location"}

    if not _has_table("applications") or not _has_table("job_matches"):
        return False

    app_columns = {column["name"] for column in _inspector().get_columns("applications")}
    match_columns = {column["name"] for column in _inspector().get_columns("job_matches")}

    return required_app_columns.issubset(app_columns) and required_match_columns.issubset(match_columns)


def _backfill_opportunity_locations() -> None:
    if not _can_backfill_from_job_matches():
        return

    bind = op.get_bind()
    app_rows = bind.execute(
        sa.text(
            """
            SELECT id, user_id, opportunity_id
            FROM applications
            WHERE opportunity_location IS NULL
              AND user_id IS NOT NULL
            """
        )
    ).fetchall()

    for row in app_rows:
        row_values = row._mapping
        location = bind.execute(
            sa.text(
                """
                SELECT location
                FROM job_matches
                WHERE user_id = :user_id
                  AND external_job_id = :opportunity_id
                  AND location IS NOT NULL
                LIMIT 1
                """
            ),
            {
                "user_id": row_values["user_id"],
                "opportunity_id": row_values["opportunity_id"],
            },
        ).scalar()

        if not location:
            continue

        bind.execute(
            sa.text(
                """
                UPDATE applications
                SET opportunity_location = :location
                WHERE id = :application_id
                  AND opportunity_location IS NULL
                """
            ),
            {
                "location": location,
                "application_id": row_values["id"],
            },
        )


def upgrade() -> None:
    if not _has_table("applications"):
        return

    if not _has_column("applications", "opportunity_location"):
        op.add_column(
            "applications",
            sa.Column("opportunity_location", sa.String(length=255), nullable=True),
        )

    _backfill_opportunity_locations()


def downgrade() -> None:
    if not _has_table("applications"):
        return
    if not _has_column("applications", "opportunity_location"):
        return

    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_column("opportunity_location")
