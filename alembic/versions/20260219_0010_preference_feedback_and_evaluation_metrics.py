"""preference feedback and evaluation metrics

Revision ID: 20260219_0010
Revises: 20260219_0009
Create Date: 2026-02-19 18:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260219_0010"
down_revision = "20260219_0009"
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


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        index.get("name") == index_name
        for index in _inspector().get_indexes(table_name)
    )


def upgrade() -> None:
    if _has_table("preference_feedback"):
        if not _has_column("preference_feedback", "node_type"):
            op.add_column(
                "preference_feedback",
                sa.Column("node_type", sa.String(length=64), nullable=True),
            )
        if not _has_column("preference_feedback", "canonical_key"):
            op.add_column(
                "preference_feedback",
                sa.Column("canonical_key", sa.String(length=255), nullable=True),
            )
        if not _has_column("preference_feedback", "resume_sha256"):
            op.add_column(
                "preference_feedback",
                sa.Column("resume_sha256", sa.String(length=64), nullable=True),
            )
        if not _has_index(
            "preference_feedback",
            "ix_preference_feedback_user_node_key_created_at",
        ):
            op.create_index(
                "ix_preference_feedback_user_node_key_created_at",
                "preference_feedback",
                ["user_id", "node_type", "canonical_key", "created_at"],
            )
        if not _has_index(
            "preference_feedback",
            "ix_preference_feedback_user_resume_sha_created_at",
        ):
            op.create_index(
                "ix_preference_feedback_user_resume_sha_created_at",
                "preference_feedback",
                ["user_id", "resume_sha256", "created_at"],
            )

    if not _has_table("recommendation_impressions"):
        op.create_table(
            "recommendation_impressions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("external_job_id", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("variant", sa.String(length=32), nullable=False),
            sa.Column("hard_constraint_violation", sa.Boolean(), nullable=False),
            sa.Column("displayed_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "run_id",
                "external_job_id",
                name="uq_recommendation_impressions_user_run_job",
            ),
        )
        op.create_index(
            "ix_recommendation_impressions_user_displayed_at",
            "recommendation_impressions",
            ["user_id", "displayed_at"],
        )
        op.create_index(
            "ix_recommendation_impressions_user_run",
            "recommendation_impressions",
            ["user_id", "run_id"],
        )

    if not _has_table("recommendation_events"):
        op.create_table(
            "recommendation_events",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=True),
            sa.Column("external_job_id", sa.String(length=128), nullable=True),
            sa.Column("application_id", sa.String(length=64), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_recommendation_events_user_occurred_at",
            "recommendation_events",
            ["user_id", "occurred_at"],
        )
        op.create_index(
            "ix_recommendation_events_user_job_occurred_at",
            "recommendation_events",
            ["user_id", "external_job_id", "occurred_at"],
        )
        op.create_index(
            "ix_recommendation_events_user_event_occurred_at",
            "recommendation_events",
            ["user_id", "event_type", "occurred_at"],
        )

    if not _has_table("evaluation_metric_snapshots"):
        op.create_table(
            "evaluation_metric_snapshots",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("window_days", sa.Integer(), nullable=False),
            sa.Column("impressions", sa.Integer(), nullable=False),
            sa.Column("clicks", sa.Integer(), nullable=False),
            sa.Column("applications_submitted", sa.Integer(), nullable=False),
            sa.Column("precision_at_5", sa.Float(), nullable=False),
            sa.Column("precision_at_10", sa.Float(), nullable=False),
            sa.Column("ndcg_at_10", sa.Float(), nullable=False),
            sa.Column("hard_constraint_violation_rate", sa.Float(), nullable=False),
            sa.Column("ctr", sa.Float(), nullable=False),
            sa.Column("apply_through_rate", sa.Float(), nullable=False),
            sa.Column("gate_status", sa.String(length=32), nullable=False),
            sa.Column("gate_checks_json", sa.Text(), nullable=False),
            sa.Column("computed_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_evaluation_metric_snapshots_user_computed_at",
            "evaluation_metric_snapshots",
            ["user_id", "computed_at"],
        )
        op.create_index(
            "ix_evaluation_metric_snapshots_user_window_computed_at",
            "evaluation_metric_snapshots",
            ["user_id", "window_days", "computed_at"],
        )


def downgrade() -> None:
    if _has_table("evaluation_metric_snapshots"):
        if _has_index(
            "evaluation_metric_snapshots",
            "ix_evaluation_metric_snapshots_user_window_computed_at",
        ):
            op.drop_index(
                "ix_evaluation_metric_snapshots_user_window_computed_at",
                table_name="evaluation_metric_snapshots",
            )
        if _has_index(
            "evaluation_metric_snapshots",
            "ix_evaluation_metric_snapshots_user_computed_at",
        ):
            op.drop_index(
                "ix_evaluation_metric_snapshots_user_computed_at",
                table_name="evaluation_metric_snapshots",
            )
        op.drop_table("evaluation_metric_snapshots")

    if _has_table("recommendation_events"):
        if _has_index(
            "recommendation_events",
            "ix_recommendation_events_user_event_occurred_at",
        ):
            op.drop_index(
                "ix_recommendation_events_user_event_occurred_at",
                table_name="recommendation_events",
            )
        if _has_index(
            "recommendation_events",
            "ix_recommendation_events_user_job_occurred_at",
        ):
            op.drop_index(
                "ix_recommendation_events_user_job_occurred_at",
                table_name="recommendation_events",
            )
        if _has_index(
            "recommendation_events",
            "ix_recommendation_events_user_occurred_at",
        ):
            op.drop_index(
                "ix_recommendation_events_user_occurred_at",
                table_name="recommendation_events",
            )
        op.drop_table("recommendation_events")

    if _has_table("recommendation_impressions"):
        if _has_index(
            "recommendation_impressions",
            "ix_recommendation_impressions_user_run",
        ):
            op.drop_index(
                "ix_recommendation_impressions_user_run",
                table_name="recommendation_impressions",
            )
        if _has_index(
            "recommendation_impressions",
            "ix_recommendation_impressions_user_displayed_at",
        ):
            op.drop_index(
                "ix_recommendation_impressions_user_displayed_at",
                table_name="recommendation_impressions",
            )
        op.drop_table("recommendation_impressions")

    if _has_table("preference_feedback"):
        if _has_index(
            "preference_feedback",
            "ix_preference_feedback_user_resume_sha_created_at",
        ):
            op.drop_index(
                "ix_preference_feedback_user_resume_sha_created_at",
                table_name="preference_feedback",
            )
        if _has_index(
            "preference_feedback",
            "ix_preference_feedback_user_node_key_created_at",
        ):
            op.drop_index(
                "ix_preference_feedback_user_node_key_created_at",
                table_name="preference_feedback",
            )
        with op.batch_alter_table("preference_feedback") as batch_op:
            if _has_column("preference_feedback", "resume_sha256"):
                batch_op.drop_column("resume_sha256")
            if _has_column("preference_feedback", "canonical_key"):
                batch_op.drop_column("canonical_key")
            if _has_column("preference_feedback", "node_type"):
                batch_op.drop_column("node_type")
