"""main platform schema

Revision ID: 20260217_0001
Revises:
Create Date: 2026-02-17 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("full_name", sa.String(length=255), nullable=False),
            sa.Column("email", sa.String(length=255), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table("user_preferences"):
        op.create_table(
            "user_preferences",
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("interests_json", sa.Text(), nullable=False),
            sa.Column("locations_json", sa.Text(), nullable=False),
            sa.Column("seniority", sa.String(length=64), nullable=True),
            sa.Column("applications_per_day", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table("resumes"):
        op.create_table(
            "resumes",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False, unique=True),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("resume_text", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table("external_run_refs"):
        op.create_table(
            "external_run_refs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("run_type", sa.String(length=16), nullable=False),
            sa.Column("external_run_id", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("request_payload_json", sa.Text(), nullable=False),
            sa.Column("latest_response_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_type", "external_run_id", name="uq_external_run_refs_type_id"),
        )

    if not _has_table("job_matches"):
        op.create_table(
            "job_matches",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("external_run_id", sa.String(length=64), nullable=False),
            sa.Column("external_job_id", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("company", sa.String(length=255), nullable=False),
            sa.Column("location", sa.String(length=255), nullable=True),
            sa.Column("apply_url", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("posted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "external_run_id",
                "external_job_id",
                name="uq_job_matches_user_run_job",
            ),
        )

    if not _has_table("application_attempts"):
        op.create_table(
            "application_attempts",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("external_run_id", sa.String(length=64), nullable=False),
            sa.Column("external_job_id", sa.String(length=128), nullable=True),
            sa.Column("job_url", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("failure_code", sa.String(length=64), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("artifacts_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if not _has_table("webhook_events"):
        op.create_table(
            "webhook_events",
            sa.Column("idempotency_key", sa.String(length=128), primary_key=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("external_run_id", sa.String(length=64), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if _has_table("webhook_events"):
        op.drop_table("webhook_events")
    if _has_table("application_attempts"):
        op.drop_table("application_attempts")
    if _has_table("job_matches"):
        op.drop_table("job_matches")
    if _has_table("external_run_refs"):
        op.drop_table("external_run_refs")
    if _has_table("resumes"):
        op.drop_table("resumes")
    if _has_table("user_preferences"):
        op.drop_table("user_preferences")
    if _has_table("users"):
        op.drop_table("users")
