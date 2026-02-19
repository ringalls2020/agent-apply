"""preference graph schema

Revision ID: 20260219_0009
Revises: 20260219_0008
Create Date: 2026-02-19 16:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260219_0009"
down_revision = "20260219_0008"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def upgrade() -> None:
    if not _has_table("preference_profile"):
        op.create_table(
            "preference_profile",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("semantic_vector_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "version",
                name="uq_preference_profile_user_version",
            ),
        )
        op.create_index(
            "ix_preference_profile_user_status",
            "preference_profile",
            ["user_id", "status"],
        )

    if not _has_table("preference_node"):
        op.create_table(
            "preference_node",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("node_type", sa.String(length=64), nullable=False),
            sa.Column("canonical_key", sa.String(length=255), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("attributes_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "node_type",
                "canonical_key",
                name="uq_preference_node_user_type_key",
            ),
        )
        op.create_index(
            "ix_preference_node_user_type",
            "preference_node",
            ["user_id", "node_type"],
        )

    if not _has_table("preference_edge"):
        op.create_table(
            "preference_edge",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("profile_id", sa.String(length=36), sa.ForeignKey("preference_profile.id"), nullable=False),
            sa.Column("node_id", sa.String(length=36), sa.ForeignKey("preference_node.id"), nullable=False),
            sa.Column("relationship", sa.String(length=32), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("hard_constraint", sa.Boolean(), nullable=False),
            sa.Column("priority", sa.Integer(), nullable=False),
            sa.Column("valid_from", sa.DateTime(), nullable=True),
            sa.Column("valid_to", sa.DateTime(), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "profile_id",
                "node_id",
                "relationship",
                "source",
                name="uq_preference_edge_profile_node_relationship_source",
            ),
        )
        op.create_index(
            "ix_preference_edge_user_profile",
            "preference_edge",
            ["user_id", "profile_id"],
        )
        op.create_index(
            "ix_preference_edge_profile",
            "preference_edge",
            ["profile_id"],
        )
        op.create_index(
            "ix_preference_edge_node",
            "preference_edge",
            ["node_id"],
        )

    if not _has_table("preference_evidence"):
        op.create_table(
            "preference_evidence",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("resume_id", sa.String(length=36), sa.ForeignKey("resumes.id"), nullable=True),
            sa.Column("node_id", sa.String(length=36), sa.ForeignKey("preference_node.id"), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("extractor_version", sa.String(length=64), nullable=False),
            sa.Column("span_ref", sa.String(length=128), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_preference_evidence_user_node",
            "preference_evidence",
            ["user_id", "node_id"],
        )

    if not _has_table("preference_feedback"):
        op.create_table(
            "preference_feedback",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("profile_id", sa.String(length=36), sa.ForeignKey("preference_profile.id"), nullable=True),
            sa.Column("node_id", sa.String(length=36), sa.ForeignKey("preference_node.id"), nullable=True),
            sa.Column("edge_id", sa.String(length=36), sa.ForeignKey("preference_edge.id"), nullable=True),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column("feedback_source", sa.String(length=32), nullable=False),
            sa.Column("detail_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_preference_feedback_user_created_at",
            "preference_feedback",
            ["user_id", "created_at"],
        )

    if not _has_table("job_match_explanations"):
        op.create_table(
            "job_match_explanations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("external_run_id", sa.String(length=64), nullable=False),
            sa.Column("external_job_id", sa.String(length=128), nullable=False),
            sa.Column("graph_score", sa.Float(), nullable=False),
            sa.Column("semantic_score", sa.Float(), nullable=False),
            sa.Column("final_score", sa.Float(), nullable=False),
            sa.Column("explanations_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "user_id",
                "external_run_id",
                "external_job_id",
                name="uq_job_match_explanations_user_run_job",
            ),
        )
        op.create_index(
            "ix_job_match_explanations_user_run",
            "job_match_explanations",
            ["user_id", "external_run_id"],
        )


def downgrade() -> None:
    if _has_table("job_match_explanations"):
        op.drop_index("ix_job_match_explanations_user_run", table_name="job_match_explanations")
        op.drop_table("job_match_explanations")

    if _has_table("preference_feedback"):
        op.drop_index("ix_preference_feedback_user_created_at", table_name="preference_feedback")
        op.drop_table("preference_feedback")

    if _has_table("preference_evidence"):
        op.drop_index("ix_preference_evidence_user_node", table_name="preference_evidence")
        op.drop_table("preference_evidence")

    if _has_table("preference_edge"):
        op.drop_index("ix_preference_edge_node", table_name="preference_edge")
        op.drop_index("ix_preference_edge_profile", table_name="preference_edge")
        op.drop_index("ix_preference_edge_user_profile", table_name="preference_edge")
        op.drop_table("preference_edge")

    if _has_table("preference_node"):
        op.drop_index("ix_preference_node_user_type", table_name="preference_node")
        op.drop_table("preference_node")

    if _has_table("preference_profile"):
        op.drop_index("ix_preference_profile_user_status", table_name="preference_profile")
        op.drop_table("preference_profile")
