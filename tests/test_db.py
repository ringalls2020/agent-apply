from backend.db import DEFAULT_DATABASE_URL, get_database_url
from backend.db_models import (
    ApplicationRecordRow,
    EvaluationMetricSnapshotRow,
    JobMatchExplanationRow,
    PreferenceEdgeRow,
    PreferenceEvidenceRow,
    PreferenceFeedbackRow,
    PreferenceNodeRow,
    PreferenceProfileRow,
    RecommendationEventRow,
    RecommendationImpressionRow,
)
from cloud_automation.db import (
    DEFAULT_DATABASE_URL as CLOUD_DEFAULT_DATABASE_URL,
    get_database_url as get_cloud_database_url,
)


def test_get_database_url_prefers_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres@localhost:5432/from_env",
    )

    result = get_database_url(
        "postgresql+psycopg://postgres@localhost:5432/from_override",
    )

    assert result == "postgresql+psycopg://postgres@localhost:5432/from_override"


def test_get_database_url_uses_default_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = get_database_url()

    assert result == DEFAULT_DATABASE_URL


def test_cloud_get_database_url_prefers_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "JOBS_DATABASE_URL",
        "postgresql+psycopg://postgres@localhost:5432/from_jobs_env",
    )

    result = get_cloud_database_url(
        "postgresql+psycopg://postgres@localhost:5432/from_jobs_override",
    )

    assert result == "postgresql+psycopg://postgres@localhost:5432/from_jobs_override"


def test_cloud_get_database_url_uses_default_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("JOBS_DATABASE_URL", raising=False)

    result = get_cloud_database_url()

    assert result == CLOUD_DEFAULT_DATABASE_URL


def test_applications_opportunity_id_column_allows_long_external_ids() -> None:
    assert ApplicationRecordRow.__table__.c.opportunity_id.type.length == 128


def test_applications_opportunity_location_column_exists() -> None:
    column = ApplicationRecordRow.__table__.c.opportunity_location
    assert column.nullable is True
    assert column.type.length == 255


def test_preference_graph_tables_are_declared() -> None:
    assert PreferenceProfileRow.__tablename__ == "preference_profile"
    assert PreferenceNodeRow.__tablename__ == "preference_node"
    assert PreferenceEdgeRow.__tablename__ == "preference_edge"
    assert PreferenceEvidenceRow.__tablename__ == "preference_evidence"
    assert PreferenceFeedbackRow.__tablename__ == "preference_feedback"
    assert JobMatchExplanationRow.__tablename__ == "job_match_explanations"


def test_preference_edge_row_has_weighted_constraint_columns() -> None:
    columns = PreferenceEdgeRow.__table__.c
    assert "weight" in columns
    assert "confidence" in columns
    assert "hard_constraint" in columns
    assert "relationship" in columns


def test_preference_feedback_has_suppression_lookup_columns_and_indexes() -> None:
    columns = PreferenceFeedbackRow.__table__.c
    assert "node_type" in columns
    assert "canonical_key" in columns
    assert "resume_sha256" in columns

    index_names = {index.name for index in PreferenceFeedbackRow.__table__.indexes}
    assert "ix_preference_feedback_user_created_at" in index_names
    assert "ix_preference_feedback_user_node_key_created_at" in index_names
    assert "ix_preference_feedback_user_resume_sha_created_at" in index_names


def test_recommendation_instrumentation_tables_are_declared() -> None:
    assert RecommendationImpressionRow.__tablename__ == "recommendation_impressions"
    assert RecommendationEventRow.__tablename__ == "recommendation_events"
    assert EvaluationMetricSnapshotRow.__tablename__ == "evaluation_metric_snapshots"
