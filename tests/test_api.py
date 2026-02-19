import base64
from collections.abc import Iterator
from datetime import timedelta
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from common.time import utc_now
from backend.db_models import (
    EvaluationMetricSnapshotRow,
    ExternalRunRefRow,
    JobMatchExplanationRow,
    PreferenceEdgeRow,
    PreferenceEvidenceRow,
    PreferenceFeedbackRow,
    PreferenceNodeRow,
    PreferenceProfileRow,
    RecommendationEventRow,
    RecommendationImpressionRow,
    ResumeRow,
)
from backend.main import create_app
from backend.models import (
    ApplicationRecord,
    ApplicationStatus,
    CloudApplyRunCreated,
    CloudMatchRunCreated,
    CloudMatchRunStatus,
    MatchRunStatus,
    MatchedJob,
    Opportunity,
)


class FakeCloudClient:
    def __init__(self) -> None:
        self._next_run = 1
        self._runs: dict[str, list[MatchedJob]] = {}
        self.apply_run_starts = 0
        self.last_apply_payload = None
        self.discovery_run_calls = 0
        self.discovery_kick_calls = 0
        self.next_match_locations: list[str] | None = None

    def run_discovery_now(self) -> dict[str, bool]:
        self.discovery_run_calls += 1
        return {"accepted": True}

    def kick_discovery(self) -> dict[str, object]:
        self.discovery_kick_calls += 1
        return {"accepted": True, "request_id": f"kick-{self.discovery_kick_calls}"}

    def start_match_run(self, payload) -> CloudMatchRunCreated:
        run_id = f"match-run-{self._next_run}"
        self._next_run += 1

        interests = payload.preferences.get("interests") or ["software"]
        preference_locations = [
            str(item).strip()
            for item in (payload.preferences.get("locations") or [])
            if str(item).strip()
        ]
        matches: list[MatchedJob] = []
        for idx in range(payload.limit):
            keyword = str(interests[idx % len(interests)]).title()
            if self.next_match_locations:
                location = self.next_match_locations[idx % len(self.next_match_locations)]
            elif payload.location:
                location = payload.location
            elif preference_locations:
                location = preference_locations[idx % len(preference_locations)]
            else:
                location = "United States"
            matches.append(
                MatchedJob(
                    external_job_id=f"{run_id}-job-{idx + 1}",
                    title=f"{keyword} Engineer {idx + 1}",
                    company=f"Live Board {idx + 1}",
                    location=location,
                    apply_url=f"https://jobs.live-board.test/{idx + 1}",
                    source="greenhouse",
                    reason="Synthetic cloud fixture for API tests",
                    score=max(0.1, 1.0 - (idx * 0.05)),
                )
            )

        self.next_match_locations = None
        self._runs[run_id] = matches
        return CloudMatchRunCreated(
            run_id=run_id,
            status=MatchRunStatus.queued,
            status_url="/graphql",
        )

    def get_match_run(self, run_id: str) -> CloudMatchRunStatus:
        matches = self._runs.get(run_id, [])
        return CloudMatchRunStatus(
            run_id=run_id,
            status=MatchRunStatus.completed,
            matches=matches,
        )

    def start_apply_run(self, payload) -> CloudApplyRunCreated:
        self.last_apply_payload = payload
        self.apply_run_starts += 1
        run_id = f"apply-run-{self.apply_run_starts}"
        return CloudApplyRunCreated(
            run_id=run_id,
            status=MatchRunStatus.queued,
            status_url="/graphql",
        )


@pytest.fixture
def test_client() -> Iterator[TestClient]:
    os.environ.setdefault("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    fake_cloud = FakeCloudClient()
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        cloud_client=fake_cloud,
    )
    app.state.test_fake_cloud_client = fake_cloud
    with TestClient(app) as client:
        yield client


def _graphql(
    client: TestClient,
    query: str,
    variables: dict | None = None,
    token: str | None = None,
) -> dict:
    headers = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    response = client.post(
        "/graphql",
        json={"query": query, "variables": variables or {}},
        headers=headers,
    )
    assert response.status_code == 200
    return response.json()


def _signup_user(
    client: TestClient,
    *,
    full_name: str,
    email: str,
    password: str = "strong-password",
) -> dict:
    result = _graphql(
        client,
        """
        mutation Signup($fullName: String!, $email: String!, $password: String!) {
          signup(fullName: $fullName, email: $email, password: $password) {
            token
            user {
              id
              fullName
              email
            }
          }
        }
        """,
        {
            "fullName": full_name,
            "email": email,
            "password": password,
        },
    )
    assert "errors" not in result
    return result["data"]["signup"]


def _seed_profile(
    client: TestClient,
    *,
    token: str,
    applications_per_day: int = 3,
    locations: list[str] | None = None,
    include_application_profile: bool = False,
) -> None:
    preferences_result = _graphql(
        client,
        """
        mutation UpdatePreferences(
          $interests: [String!]!
          $applicationsPerDay: Int!
          $locations: [String!]
        ) {
          updatePreferences(
            interests: $interests
            applicationsPerDay: $applicationsPerDay
            locations: $locations
          ) {
            userId
            interests
            locations
            applicationsPerDay
          }
        }
        """,
        {
            "interests": ["ai", "climate"],
            "applicationsPerDay": applications_per_day,
            "locations": locations,
        },
        token=token,
    )
    resume_result = _graphql(
        client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
            filename
          }
        }
        """,
        {
            "filename": "resume.txt",
            "resumeText": "ML engineer with automation and platform experience.",
        },
        token=token,
    )
    profile_result = None
    if include_application_profile:
        profile_result = _graphql(
            client,
            """
            mutation UpdateProfile($input: ApplicationProfileInput!) {
              updateProfile(input: $input) {
                autosubmitEnabled
              }
            }
            """,
            {"input": {"autosubmitEnabled": False}},
            token=token,
        )
    assert "errors" not in preferences_result
    assert "errors" not in resume_result
    if profile_result is not None:
        assert "errors" not in profile_result


def test_graphql_upload_resume_supports_base64_text_payload(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Binary Resume",
        email="binary-resume@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    resume_text = "ML engineer with automation and platform experience."
    encoded = base64.b64encode(resume_text.encode("utf-8")).decode("ascii")

    result = _graphql(
        test_client,
        """
        mutation UploadResume(
          $filename: String!
          $fileContentBase64: String
          $fileMimeType: String
        ) {
          uploadResume(
            filename: $filename
            fileContentBase64: $fileContentBase64
            fileMimeType: $fileMimeType
          ) {
            id
            filename
            resumeText
          }
        }
        """,
        {
            "filename": "resume.txt",
            "fileContentBase64": encoded,
            "fileMimeType": "text/plain",
        },
        token=token,
    )
    assert "errors" not in result
    assert result["data"]["uploadResume"]["filename"] == "resume.txt"
    assert "ML engineer" in result["data"]["uploadResume"]["resumeText"]

    with test_client.app.state.main_store._session_factory() as session:
        row = session.scalar(select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1))
        assert row is not None
        assert row.resume_text.startswith("ML engineer")
        assert row.file_bytes is not None
        assert row.file_mime_type == "text/plain"
        assert row.file_size_bytes == len(resume_text.encode("utf-8"))
        assert row.file_sha256


def test_upload_resume_populates_preference_graph_rows(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Resume",
        email="graph-resume@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    result = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {
            "filename": "resume.txt",
            "resumeText": (
                "Senior backend engineer focused on Python and FastAPI. "
                "Remote work preferred and authorized to work in United States."
            ),
        },
        token=token,
    )
    assert "errors" not in result

    with test_client.app.state.main_store._session_factory() as session:
        profile = session.scalar(
            select(PreferenceProfileRow)
            .where(
                PreferenceProfileRow.user_id == user_id,
                PreferenceProfileRow.status == "active",
            )
            .limit(1)
        )
        assert profile is not None

        edges = session.scalars(
            select(PreferenceEdgeRow).where(PreferenceEdgeRow.profile_id == profile.id)
        ).all()
        assert edges
        assert any(edge.source == "resume_parse" for edge in edges)

        node_ids = [edge.node_id for edge in edges]
        nodes = session.scalars(
            select(PreferenceNodeRow).where(PreferenceNodeRow.id.in_(node_ids))
        ).all()
        canonical_keys = {node.canonical_key for node in nodes}
        assert "python" in canonical_keys
        assert any(key in canonical_keys for key in {"remote", "united-states"})

        evidence_rows = session.scalars(
            select(PreferenceEvidenceRow).where(PreferenceEvidenceRow.user_id == user_id)
        ).all()
        assert evidence_rows


def test_update_preferences_creates_manual_override_edges(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Manual Override",
        email="graph-manual-override@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    upload = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {
            "filename": "resume.txt",
            "resumeText": "Python backend engineer building APIs.",
        },
        token=token,
    )
    assert "errors" not in upload

    update = _graphql(
        test_client,
        """
        mutation UpdatePreferences(
          $interests: [String!]!
          $locations: [String!]
          $applicationsPerDay: Int!
        ) {
          updatePreferences(
            interests: $interests
            locations: $locations
            applicationsPerDay: $applicationsPerDay
          ) {
            userId
          }
        }
        """,
        {
            "interests": ["python", "platform"],
            "locations": ["Canada"],
            "applicationsPerDay": 5,
        },
        token=token,
    )
    assert "errors" not in update

    with test_client.app.state.main_store._session_factory() as session:
        profile = session.scalar(
            select(PreferenceProfileRow)
            .where(
                PreferenceProfileRow.user_id == user_id,
                PreferenceProfileRow.status == "active",
            )
            .limit(1)
        )
        assert profile is not None

        edges = session.scalars(
            select(PreferenceEdgeRow).where(PreferenceEdgeRow.profile_id == profile.id)
        ).all()
        assert any(edge.source == "manual" for edge in edges)
        assert any(edge.source == "manual" and edge.relationship == "overrides" for edge in edges)

        location_edge = next(
            (
                edge
                for edge in edges
                if edge.source == "manual" and edge.hard_constraint
            ),
            None,
        )
        assert location_edge is not None


def test_graphql_inferred_preferences_query_returns_pending_items(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Inference Query User",
        email="inference-query@example.com",
    )
    token = signup["token"]

    upload = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {
            "filename": "resume.txt",
            "resumeText": (
                "Python backend engineer. Remote preferred in Canada and authorized to work in United States."
            ),
        },
        token=token,
    )
    assert "errors" not in upload

    inferred = _graphql(
        test_client,
        """
        query Inferred($status: InferredPreferenceStatus) {
          inferredPreferences(status: $status) {
            edgeId
            nodeType
            canonicalKey
            label
            status
          }
        }
        """,
        {"status": "PENDING"},
        token=token,
    )
    assert "errors" not in inferred
    items = inferred["data"]["inferredPreferences"]
    assert items
    assert all(item["status"] == "PENDING" for item in items)
    assert any(item["nodeType"] == "skill" for item in items)
    assert any(item["canonicalKey"] == "python" for item in items)


def test_confirm_inferred_preferences_accept_records_feedback_and_updates_locations(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Inference Accept User",
        email="inference-accept@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    upload = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {
            "filename": "resume.txt",
            "resumeText": (
                "Backend engineer with Python experience. Remote preferred and open to Canada."
            ),
        },
        token=token,
    )
    assert "errors" not in upload

    inferred = _graphql(
        test_client,
        """
        query Pending {
          inferredPreferences(status: PENDING) {
            edgeId
            nodeType
            label
          }
        }
        """,
        token=token,
    )
    assert "errors" not in inferred
    pending_items = inferred["data"]["inferredPreferences"]
    location_item = next((item for item in pending_items if item["nodeType"] == "location"), None)
    assert location_item is not None

    confirm = _graphql(
        test_client,
        """
        mutation Confirm($actions: [InferredPreferenceDecisionInput!]!) {
          confirmInferredPreferences(actions: $actions) {
            acceptedCount
            rejectedCount
            editedCount
            remainingPendingCount
          }
        }
        """,
        {
            "actions": [
                {
                    "edgeId": location_item["edgeId"],
                    "decision": "ACCEPT",
                }
            ]
        },
        token=token,
    )
    assert "errors" not in confirm
    payload = confirm["data"]["confirmInferredPreferences"]
    assert payload["acceptedCount"] == 1
    assert payload["rejectedCount"] == 0
    assert payload["editedCount"] == 0

    me = _graphql(
        test_client,
        """
        query Me {
          me {
            locations
          }
        }
        """,
        token=token,
    )
    assert "errors" not in me
    assert location_item["label"] in me["data"]["me"]["locations"]

    with test_client.app.state.main_store._session_factory() as session:
        feedback = session.scalar(
            select(PreferenceFeedbackRow)
            .where(
                PreferenceFeedbackRow.user_id == user_id,
                PreferenceFeedbackRow.decision == "accept",
                PreferenceFeedbackRow.node_type == "location",
            )
            .order_by(PreferenceFeedbackRow.created_at.desc())
            .limit(1)
        )
        assert feedback is not None


def test_confirm_inferred_preferences_edit_updates_manual_interest_and_feedback(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Inference Edit User",
        email="inference-edit@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    upload = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {
            "filename": "resume.txt",
            "resumeText": "Python engineer with FastAPI and backend automation experience.",
        },
        token=token,
    )
    assert "errors" not in upload

    inferred = _graphql(
        test_client,
        """
        query Pending {
          inferredPreferences(status: PENDING) {
            edgeId
            nodeType
            canonicalKey
          }
        }
        """,
        token=token,
    )
    assert "errors" not in inferred
    skill_item = next(
        (
            item
            for item in inferred["data"]["inferredPreferences"]
            if item["nodeType"] == "skill" and item["canonicalKey"] == "python"
        ),
        None,
    )
    assert skill_item is not None

    edited_label = "Python Platform Engineering"
    confirm = _graphql(
        test_client,
        """
        mutation Confirm($actions: [InferredPreferenceDecisionInput!]!) {
          confirmInferredPreferences(actions: $actions) {
            acceptedCount
            rejectedCount
            editedCount
            remainingPendingCount
          }
        }
        """,
        {
            "actions": [
                {
                    "edgeId": skill_item["edgeId"],
                    "decision": "EDIT",
                    "editedLabel": edited_label,
                }
            ]
        },
        token=token,
    )
    assert "errors" not in confirm
    payload = confirm["data"]["confirmInferredPreferences"]
    assert payload["acceptedCount"] == 0
    assert payload["rejectedCount"] == 0
    assert payload["editedCount"] == 1

    me = _graphql(
        test_client,
        """
        query Me {
          me {
            interests
          }
        }
        """,
        token=token,
    )
    assert "errors" not in me
    assert edited_label in me["data"]["me"]["interests"]

    with test_client.app.state.main_store._session_factory() as session:
        feedback = session.scalar(
            select(PreferenceFeedbackRow)
            .where(
                PreferenceFeedbackRow.user_id == user_id,
                PreferenceFeedbackRow.decision == "edit",
                PreferenceFeedbackRow.node_type == "skill",
            )
            .order_by(PreferenceFeedbackRow.created_at.desc())
            .limit(1)
        )
        assert feedback is not None
        detail = json.loads(feedback.detail_json)
        assert detail["edited_label"] == edited_label


def test_confirm_inferred_preferences_reject_suppresses_same_resume_fingerprint(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Inference Reject User",
        email="inference-reject@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    resume_text = "Python backend engineer with API and automation experience."

    first_upload = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {"filename": "resume.txt", "resumeText": resume_text},
        token=token,
    )
    assert "errors" not in first_upload

    pending = _graphql(
        test_client,
        """
        query Pending {
          inferredPreferences(status: PENDING) {
            edgeId
            nodeType
            canonicalKey
          }
        }
        """,
        token=token,
    )
    assert "errors" not in pending
    skill_item = next(
        (
            item
            for item in pending["data"]["inferredPreferences"]
            if item["nodeType"] == "skill" and item["canonicalKey"] == "python"
        ),
        None,
    )
    assert skill_item is not None

    reject = _graphql(
        test_client,
        """
        mutation Confirm($actions: [InferredPreferenceDecisionInput!]!) {
          confirmInferredPreferences(actions: $actions) {
            rejectedCount
            remainingPendingCount
          }
        }
        """,
        {
            "actions": [
                {
                    "edgeId": skill_item["edgeId"],
                    "decision": "REJECT",
                }
            ]
        },
        token=token,
    )
    assert "errors" not in reject
    assert reject["data"]["confirmInferredPreferences"]["rejectedCount"] == 1

    republished = _graphql(
        test_client,
        """
        mutation UploadResume($filename: String!, $resumeText: String!) {
          uploadResume(filename: $filename, resumeText: $resumeText) {
            id
          }
        }
        """,
        {"filename": "resume.txt", "resumeText": resume_text},
        token=token,
    )
    assert "errors" not in republished

    pending_after = _graphql(
        test_client,
        """
        query Pending {
          inferredPreferences(status: PENDING) {
            nodeType
            canonicalKey
          }
        }
        """,
        token=token,
    )
    assert "errors" not in pending_after
    canonical_keys = {
        (item["nodeType"], item["canonicalKey"])
        for item in pending_after["data"]["inferredPreferences"]
    }
    assert ("skill", "python") not in canonical_keys

    with test_client.app.state.main_store._session_factory() as session:
        feedback = session.scalar(
            select(PreferenceFeedbackRow)
            .where(
                PreferenceFeedbackRow.user_id == user_id,
                PreferenceFeedbackRow.decision == "reject",
                PreferenceFeedbackRow.node_type == "skill",
                PreferenceFeedbackRow.canonical_key == "python",
            )
            .order_by(PreferenceFeedbackRow.created_at.desc())
            .limit(1)
        )
        assert feedback is not None
        assert isinstance(feedback.resume_sha256, str)
        assert feedback.resume_sha256


def test_graphql_evaluation_metrics_returns_expected_values_for_seeded_data(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Evaluation Metrics User",
        email="evaluation-metrics@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    test_client.app.state.main_store.record_recommendation_impressions(
        user_id=user_id,
        run_id="eval-run-1",
        variant="legacy",
        matches=[
            MatchedJob(
                external_job_id="eval-job-1",
                title="Backend Engineer 1",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/eval-1",
                source="greenhouse",
                reason="seeded metrics run",
                score=0.9,
            ),
            MatchedJob(
                external_job_id="eval-job-2",
                title="Backend Engineer 2",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/eval-2",
                source="greenhouse",
                reason="seeded metrics run",
                score=0.8,
            ),
            MatchedJob(
                external_job_id="eval-job-3",
                title="Backend Engineer 3",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/eval-3",
                source="greenhouse",
                reason="seeded metrics run with hard constraint violation",
                score=0.7,
            ),
        ],
    )
    test_client.app.state.main_store.record_recommendation_event(
        user_id=user_id,
        run_id="eval-run-1",
        external_job_id="eval-job-1",
        event_type="application_viewed",
    )
    test_client.app.state.main_store.record_recommendation_event(
        user_id=user_id,
        run_id="eval-run-1",
        external_job_id="eval-job-2",
        event_type="application_submitted",
    )

    result = _graphql(
        test_client,
        """
        query Metrics($windowDays: Int!, $refresh: Boolean!) {
          evaluationMetrics(windowDays: $windowDays, refresh: $refresh) {
            windowDays
            impressions
            clicks
            applicationsSubmitted
            precisionAt5
            precisionAt10
            ndcgAt10
            hardConstraintViolationRate
            ctr
            applyThroughRate
            gateStatus
          }
        }
        """,
        {"windowDays": 14, "refresh": True},
        token=token,
    )
    assert "errors" not in result
    metrics = result["data"]["evaluationMetrics"]
    assert metrics["windowDays"] == 14
    assert metrics["impressions"] == 3
    assert metrics["clicks"] == 1
    assert metrics["applicationsSubmitted"] == 1
    assert metrics["precisionAt5"] == pytest.approx(2 / 3)
    assert metrics["precisionAt10"] == pytest.approx(2 / 3)
    assert metrics["ndcgAt10"] == pytest.approx(0.7967075809905068)
    assert metrics["hardConstraintViolationRate"] == pytest.approx(1 / 3)
    assert metrics["ctr"] == pytest.approx(1 / 3)
    assert metrics["applyThroughRate"] == pytest.approx(1 / 3)
    assert metrics["gateStatus"] == "INSUFFICIENT_DATA"

    with test_client.app.state.main_store._session_factory() as session:
        snapshot = session.scalar(
            select(EvaluationMetricSnapshotRow)
            .where(
                EvaluationMetricSnapshotRow.user_id == user_id,
                EvaluationMetricSnapshotRow.window_days == 14,
            )
            .order_by(EvaluationMetricSnapshotRow.computed_at.desc())
            .limit(1)
        )
        assert snapshot is not None


def test_graphql_evaluation_metrics_gate_status_transitions(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Evaluation Gates User",
        email="evaluation-gates@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    test_client.app.state.eval_gate_thresholds = {
        "min_impressions": 4,
        "min_runs": 2,
        "precision_at_5_min": 0.9,
        "precision_at_10_min": 0.9,
        "ndcg_at_10_min": 0.9,
        "hard_constraint_violation_max": 0.0,
        "ctr_min": 0.9,
        "apply_through_min": 0.9,
    }

    insufficient = _graphql(
        test_client,
        """
        query Metrics {
          evaluationMetrics(windowDays: 14, refresh: true) {
            gateStatus
          }
        }
        """,
        token=token,
    )
    assert "errors" not in insufficient
    assert insufficient["data"]["evaluationMetrics"]["gateStatus"] == "INSUFFICIENT_DATA"

    test_client.app.state.main_store.record_recommendation_impressions(
        user_id=user_id,
        run_id="gate-run-1",
        variant="legacy",
        matches=[
            MatchedJob(
                external_job_id="gate-job-1",
                title="Role A",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/gate-1",
                source="greenhouse",
                reason="gate metrics",
                score=0.9,
            ),
            MatchedJob(
                external_job_id="gate-job-2",
                title="Role B",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/gate-2",
                source="greenhouse",
                reason="gate metrics",
                score=0.8,
            ),
        ],
    )
    test_client.app.state.main_store.record_recommendation_impressions(
        user_id=user_id,
        run_id="gate-run-2",
        variant="legacy",
        matches=[
            MatchedJob(
                external_job_id="gate-job-3",
                title="Role C",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/gate-3",
                source="greenhouse",
                reason="gate metrics",
                score=0.7,
            ),
            MatchedJob(
                external_job_id="gate-job-4",
                title="Role D",
                company="Acme",
                location="Remote",
                apply_url="https://example.com/jobs/gate-4",
                source="greenhouse",
                reason="gate metrics",
                score=0.6,
            ),
        ],
    )

    failed = _graphql(
        test_client,
        """
        query Metrics {
          evaluationMetrics(windowDays: 14, refresh: true) {
            gateStatus
          }
        }
        """,
        token=token,
    )
    assert "errors" not in failed
    assert failed["data"]["evaluationMetrics"]["gateStatus"] == "FAIL"

    test_client.app.state.eval_gate_thresholds = {
        "min_impressions": 1,
        "min_runs": 1,
        "precision_at_5_min": 0.0,
        "precision_at_10_min": 0.0,
        "ndcg_at_10_min": 0.0,
        "hard_constraint_violation_max": 1.0,
        "ctr_min": 0.0,
        "apply_through_min": 0.0,
    }

    passed = _graphql(
        test_client,
        """
        query Metrics {
          evaluationMetrics(windowDays: 14, refresh: true) {
            gateStatus
            gateChecks {
              metric
              passed
            }
          }
        }
        """,
        token=token,
    )
    assert "errors" not in passed
    assert passed["data"]["evaluationMetrics"]["gateStatus"] == "PASS"
    assert all(check["passed"] for check in passed["data"]["evaluationMetrics"]["gateChecks"])


def test_graphql_instrumentation_writes_for_run_view_apply_paths(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Instrumentation User",
        email="instrumentation-user@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    _seed_profile(test_client, token=token, applications_per_day=3)

    run_result = _graphql(
        test_client,
        """
        mutation RunAgent {
          runAgent {
            id
            opportunity {
              id
            }
          }
        }
        """,
        token=token,
    )
    assert "errors" not in run_result
    applications = run_result["data"]["runAgent"]
    assert len(applications) >= 2

    viewed_application_id = applications[0]["id"]
    apply_selected_application_id = applications[1]["id"]

    viewed = _graphql(
        test_client,
        """
        mutation Viewed($applicationId: ID!) {
          markApplicationViewed(applicationId: $applicationId) {
            id
          }
        }
        """,
        {"applicationId": viewed_application_id},
        token=token,
    )
    assert "errors" not in viewed

    marked_applied = _graphql(
        test_client,
        """
        mutation Applied($applicationId: ID!) {
          markApplicationApplied(applicationId: $applicationId) {
            id
          }
        }
        """,
        {"applicationId": viewed_application_id},
        token=token,
    )
    assert "errors" not in marked_applied

    apply_selected = _graphql(
        test_client,
        """
        mutation Apply($applicationIds: [ID!]!) {
          applySelectedApplications(applicationIds: $applicationIds) {
            runId
            acceptedApplicationIds
          }
        }
        """,
        {"applicationIds": [apply_selected_application_id]},
        token=token,
    )
    assert "errors" not in apply_selected
    assert apply_selected["data"]["applySelectedApplications"]["acceptedApplicationIds"] == [
        apply_selected_application_id
    ]

    with test_client.app.state.main_store._session_factory() as session:
        impressions = session.scalars(
            select(RecommendationImpressionRow).where(
                RecommendationImpressionRow.user_id == user_id
            )
        ).all()
        assert impressions

        events = session.scalars(
            select(RecommendationEventRow).where(RecommendationEventRow.user_id == user_id)
        ).all()
        event_types = {event.event_type for event in events}
        assert "application_viewed" in event_types
        assert "application_applied" in event_types
        assert "application_submitted" in event_types


def test_apply_payload_includes_resume_file_and_stored_payload_is_redacted(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Redaction User",
        email="redaction-user@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    resume_text = "Platform engineer with backend and automation experience."
    encoded = base64.b64encode(resume_text.encode("utf-8")).decode("ascii")

    preferences_result = _graphql(
        test_client,
        """
        mutation UpdatePreferences($interests: [String!]!, $applicationsPerDay: Int!) {
          updatePreferences(interests: $interests, applicationsPerDay: $applicationsPerDay) {
            userId
          }
        }
        """,
        {"interests": ["platform", "backend"], "applicationsPerDay": 3},
        token=token,
    )
    assert "errors" not in preferences_result

    resume_result = _graphql(
        test_client,
        """
        mutation UploadResume(
          $filename: String!
          $fileContentBase64: String
          $fileMimeType: String
        ) {
          uploadResume(
            filename: $filename
            fileContentBase64: $fileContentBase64
            fileMimeType: $fileMimeType
          ) {
            id
            filename
          }
        }
        """,
        {
            "filename": "resume.txt",
            "fileContentBase64": encoded,
            "fileMimeType": "text/plain",
        },
        token=token,
    )
    assert "errors" not in resume_result

    run_result = _graphql(
        test_client,
        """
        mutation RunAgent {
          runAgent {
            id
          }
        }
        """,
        token=token,
    )
    assert "errors" not in run_result
    first_application_id = run_result["data"]["runAgent"][0]["id"]

    apply_result = _graphql(
        test_client,
        """
        mutation ApplySelected($applicationIds: [ID!]!) {
          applySelectedApplications(applicationIds: $applicationIds) {
            runId
          }
        }
        """,
        variables={"applicationIds": [first_application_id]},
        token=token,
    )
    assert "errors" not in apply_result
    run_id = apply_result["data"]["applySelectedApplications"]["runId"]
    assert run_id

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.last_apply_payload is not None
    resume_file_payload = fake_cloud.last_apply_payload.profile_payload.get("resume_file")
    assert isinstance(resume_file_payload, dict)
    assert resume_file_payload.get("content_base64") == encoded
    assert resume_file_payload.get("size_bytes") == len(resume_text.encode("utf-8"))

    with test_client.app.state.main_store._session_factory() as session:
        row = session.scalar(
            select(ExternalRunRefRow)
            .where(
                ExternalRunRefRow.user_id == user_id,
                ExternalRunRefRow.external_run_id == run_id,
            )
            .limit(1)
        )
        assert row is not None
        request_payload = json.loads(row.request_payload_json)
        stored_resume_file = request_payload["profile_payload"]["resume_file"]
        assert stored_resume_file["content_base64"] == "<redacted>"
        assert stored_resume_file["size_bytes"] == len(resume_text.encode("utf-8"))


def _seed_application_record(
    client: TestClient,
    *,
    user_id: str,
    app_id: str,
    opportunity_id: str,
    discovered_at_offset_days: int,
    location: str | None = None,
    status: ApplicationStatus = ApplicationStatus.review,
) -> ApplicationRecord:
    record = ApplicationRecord(
        id=app_id,
        opportunity=Opportunity(
            id=opportunity_id,
            title="Platform Engineer",
            company="Acme",
            location=location,
            url=f"https://example.com/jobs/{opportunity_id}",
            reason="seeded for tests",
            discovered_at=utc_now() - timedelta(days=discovered_at_offset_days),
        ),
        status=status,
    )
    return client.app.state.store.upsert_for_user(user_id, record)


def test_health_endpoint_returns_ok(test_client: TestClient) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_returns_generated_request_id_header(
    test_client: TestClient,
) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-request-id")


def test_agent_run_endpoint_is_removed(test_client: TestClient) -> None:
    route = next(
        (
            item
            for item in test_client.app.router.routes
            if getattr(item, "path", None) == "/v1/agent/run"
            and "POST" in getattr(item, "methods", set())
        ),
        None,
    )
    assert route is None


def test_health_endpoint_reuses_incoming_request_id_header(
    test_client: TestClient,
) -> None:
    response = test_client.get("/health", headers={"x-request-id": "req-123"})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-123"


def test_graphql_signup_login_and_me_flow(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane@example.com",
    )
    token = signup["token"]

    me = _graphql(
        test_client,
        """
        query Me {
          me {
            id
            email
            fullName
            locations
            applicationsPerDay
          }
        }
        """,
        token=token,
    )
    assert "errors" not in me
    assert me["data"]["me"]["email"] == "jane@example.com"
    assert me["data"]["me"]["locations"] == []

    login = _graphql(
        test_client,
        """
        mutation Login($email: String!, $password: String!) {
          login(email: $email, password: $password) {
            token
            user {
              id
            }
          }
        }
        """,
        {"email": "jane@example.com", "password": "strong-password"},
    )
    assert "errors" not in login
    assert login["data"]["login"]["user"]["id"] == signup["user"]["id"]


def test_graphql_update_preferences_preserves_locations_when_omitted(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Location Preserve",
        email="location-preserve@example.com",
    )
    token = signup["token"]

    first_update = _graphql(
        test_client,
        """
        mutation UpdatePreferences(
          $interests: [String!]!
          $applicationsPerDay: Int!
          $locations: [String!]
        ) {
          updatePreferences(
            interests: $interests
            applicationsPerDay: $applicationsPerDay
            locations: $locations
          ) {
            interests
            locations
            applicationsPerDay
          }
        }
        """,
        {
            "interests": ["backend", "security"],
            "applicationsPerDay": 3,
            "locations": ["Canada", "Germany"],
        },
        token=token,
    )
    assert "errors" not in first_update
    assert first_update["data"]["updatePreferences"]["locations"] == ["Canada", "Germany"]

    second_update = _graphql(
        test_client,
        """
        mutation UpdatePreferences($interests: [String!]!, $applicationsPerDay: Int!) {
          updatePreferences(interests: $interests, applicationsPerDay: $applicationsPerDay) {
            interests
            locations
            applicationsPerDay
          }
        }
        """,
        {
            "interests": ["platform"],
            "applicationsPerDay": 5,
        },
        token=token,
    )
    assert "errors" not in second_update
    assert second_update["data"]["updatePreferences"]["locations"] == ["Canada", "Germany"]

    me_result = _graphql(
        test_client,
        """
        query Me {
          me {
            interests
            locations
            applicationsPerDay
          }
        }
        """,
        token=token,
    )
    assert "errors" not in me_result
    assert me_result["data"]["me"]["locations"] == ["Canada", "Germany"]
    assert me_result["data"]["me"]["interests"] == ["platform"]
    assert me_result["data"]["me"]["applicationsPerDay"] == 5


def test_graphql_login_rejects_invalid_credentials(test_client: TestClient) -> None:
    _signup_user(test_client, full_name="Jane Doe", email="jane@example.com")
    result = _graphql(
        test_client,
        """
        mutation Login($email: String!, $password: String!) {
          login(email: $email, password: $password) {
            token
          }
        }
        """,
        {"email": "jane@example.com", "password": "wrong-password"},
    )
    assert "errors" in result
    assert result["errors"][0]["message"] == "Invalid credentials."


def test_graphql_run_agent_requires_preferences_and_resume(test_client: TestClient) -> None:
    signup = _signup_user(test_client, full_name="Graph Runner", email="graph-runner@example.com")
    result = _graphql(
        test_client,
        """
        mutation RunAgent {
          runAgent {
            id
          }
        }
        """,
        token=signup["token"],
    )
    assert "errors" in result
    assert result["errors"][0]["message"] in {
        "User preferences not found",
        "User resume not found",
    }


def test_graphql_run_agent_mutation_returns_applications(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Runner",
        email="graph-runner-seeded@example.com",
    )
    token = signup["token"]
    _seed_profile(test_client, token=token, applications_per_day=2)

    profile_result = _graphql(
        test_client,
        """
        query Profile {
          profile {
            autosubmitEnabled
          }
        }
        """,
        token=token,
    )
    assert "errors" in profile_result
    assert profile_result["errors"][0]["message"] == "Profile not found"

    result = _graphql(
        test_client,
        """
        mutation RunAgent {
          runAgent {
            id
            status
            opportunity {
              id
              title
            }
          }
        }
        """,
        token=token,
    )
    assert "errors" not in result
    assert len(result["data"]["runAgent"]) == 2


def test_graphql_run_agent_filters_results_by_multi_country_preferences(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Location Filter Runner",
        email="location-filter-runner@example.com",
    )
    token = signup["token"]
    _seed_profile(
        test_client,
        token=token,
        applications_per_day=3,
        locations=["Canada", "Germany"],
    )

    fake_cloud = test_client.app.state.test_fake_cloud_client
    fake_cloud.next_match_locations = ["Canada", "Brazil", "Germany"]

    result = _graphql(
        test_client,
        """
        mutation RunAgent {
          runAgent {
            id
            opportunity {
              location
            }
          }
        }
        """,
        token=token,
    )
    assert "errors" not in result
    locations = [item["opportunity"]["location"] for item in result["data"]["runAgent"]]
    assert len(locations) == 2
    assert set(locations) == {"Canada", "Germany"}


def test_graphql_run_agent_preference_graph_reranks_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GraphRerankCloudClient(FakeCloudClient):
        def start_match_run(self, payload) -> CloudMatchRunCreated:
            run_id = f"match-run-{self._next_run}"
            self._next_run += 1
            self._runs[run_id] = [
                MatchedJob(
                    external_job_id=f"{run_id}-job-1",
                    title="Generalist Engineer",
                    company="Live Board 1",
                    location="United States",
                    apply_url="https://jobs.live-board.test/1",
                    source="greenhouse",
                    reason="General software profile",
                    score=0.92,
                ),
                MatchedJob(
                    external_job_id=f"{run_id}-job-2",
                    title="Python Backend Engineer",
                    company="Live Board 2",
                    location="United States",
                    apply_url="https://jobs.live-board.test/2",
                    source="greenhouse",
                    reason="Python and FastAPI overlap",
                    score=0.41,
                ),
            ]
            return CloudMatchRunCreated(
                run_id=run_id,
                status=MatchRunStatus.queued,
                status_url="/graphql",
            )

    monkeypatch.setenv("USE_PREFERENCE_GRAPH_MATCHING", "true")
    monkeypatch.setenv("ENABLE_PREFERENCE_GRAPH_SHADOW_SCORING", "true")
    monkeypatch.setenv("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        cloud_client=GraphRerankCloudClient(),
    )
    with TestClient(app) as client:
        signup = _signup_user(
            client,
            full_name="Graph Rerank",
            email="graph-rerank@example.com",
        )
        token = signup["token"]
        user_id = signup["user"]["id"]

        preferences_result = _graphql(
            client,
            """
            mutation UpdatePreferences($interests: [String!]!, $applicationsPerDay: Int!) {
              updatePreferences(interests: $interests, applicationsPerDay: $applicationsPerDay) {
                userId
              }
            }
            """,
            {"interests": ["python"], "applicationsPerDay": 5},
            token=token,
        )
        assert "errors" not in preferences_result

        resume_result = _graphql(
            client,
            """
            mutation UploadResume($filename: String!, $resumeText: String!) {
              uploadResume(filename: $filename, resumeText: $resumeText) {
                id
              }
            }
            """,
            {
                "filename": "resume.txt",
                "resumeText": "Python backend engineer with FastAPI experience.",
            },
            token=token,
        )
        assert "errors" not in resume_result

        run_result = _graphql(
            client,
            """
            mutation RunAgent {
              runAgent {
                opportunity {
                  title
                  reason
                }
              }
            }
            """,
            token=token,
        )
        assert "errors" not in run_result
        opportunities = [item["opportunity"] for item in run_result["data"]["runAgent"]]
        assert opportunities
        assert opportunities[0]["title"] == "Python Backend Engineer"
        assert "graph-hybrid" in opportunities[0]["reason"]

        with client.app.state.main_store._session_factory() as session:
            explanations = session.scalars(
                select(JobMatchExplanationRow).where(
                    JobMatchExplanationRow.user_id == user_id
                )
            ).all()
            assert len(explanations) >= 2


def test_graphql_run_agent_enqueues_discovery_kick(test_client: TestClient) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Runner Kick",
        email="graph-runner-kick@example.com",
    )
    token = signup["token"]
    _seed_profile(test_client, token=token, applications_per_day=1)

    result = _graphql(
        test_client,
        "mutation RunAgent { runAgent { id } }",
        token=token,
    )

    assert "errors" not in result
    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.discovery_kick_calls == 1
    assert fake_cloud.discovery_run_calls == 0


def test_graphql_run_agent_skips_discovery_kick_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_RUN_AGENT_DISCOVERY_KICK", "false")
    monkeypatch.setenv("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    fake_cloud = FakeCloudClient()
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        cloud_client=fake_cloud,
    )
    with TestClient(app) as client:
        signup = _signup_user(
            client,
            full_name="Graph Runner No Kick",
            email="graph-runner-no-kick@example.com",
        )
        _seed_profile(client, token=signup["token"], applications_per_day=1)
        result = _graphql(
            client,
            "mutation RunAgent { runAgent { id } }",
            token=signup["token"],
        )
        assert "errors" not in result

    assert fake_cloud.discovery_kick_calls == 0


def test_graphql_run_agent_disabled_when_feature_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_DEV_RUN_AGENT", "false")
    monkeypatch.setenv("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        cloud_client=FakeCloudClient(),
    )
    with TestClient(app) as client:
        signup = _signup_user(
            client,
            full_name="Graph Runner Disabled",
            email="graph-runner-disabled@example.com",
        )
        _seed_profile(client, token=signup["token"], applications_per_day=1)
        result = _graphql(
            client,
            "mutation RunAgent { runAgent { id } }",
            token=signup["token"],
        )
        assert "errors" in result
        assert result["errors"][0]["message"] == "runAgent is disabled in this environment"


def test_graphql_applications_are_user_scoped(test_client: TestClient) -> None:
    user_a = _signup_user(test_client, full_name="A User", email="a@example.com")
    user_b = _signup_user(test_client, full_name="B User", email="b@example.com")
    _seed_profile(test_client, token=user_a["token"], applications_per_day=2)
    _seed_profile(test_client, token=user_b["token"], applications_per_day=1)

    _graphql(
        test_client,
        "mutation RunA { runAgent { id } }",
        token=user_a["token"],
    )
    _graphql(
        test_client,
        "mutation RunB { runAgent { id } }",
        token=user_b["token"],
    )

    apps_a = _graphql(
        test_client,
        "query Apps { applications { id } }",
        token=user_a["token"],
    )
    apps_b = _graphql(
        test_client,
        "query Apps { applications { id } }",
        token=user_b["token"],
    )

    ids_a = {row["id"] for row in apps_a["data"]["applications"]}
    ids_b = {row["id"] for row in apps_b["data"]["applications"]}
    assert ids_a
    assert ids_b
    assert ids_a.isdisjoint(ids_b)


def test_graphql_applications_search_hides_archived_by_default(test_client: TestClient) -> None:
    signup = _signup_user(test_client, full_name="Search User", email="search@example.com")
    token = signup["token"]
    user_id = signup["user"]["id"]

    _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="active-app",
        opportunity_id="active-job",
        discovered_at_offset_days=1,
    )
    _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-app",
        opportunity_id="archived-job",
        discovered_at_offset_days=45,
    )

    default_result = _graphql(
        test_client,
        """
        query Search($filter: ApplicationFilterInput) {
          applicationsSearch(filter: $filter, limit: 25, offset: 0) {
            applications {
              id
              isArchived
            }
            totalCount
          }
        }
        """,
        {"filter": {}},
        token=token,
    )
    include_archived_result = _graphql(
        test_client,
        """
        query Search($filter: ApplicationFilterInput) {
          applicationsSearch(filter: $filter, limit: 25, offset: 0) {
            applications {
              id
              isArchived
            }
            totalCount
          }
        }
        """,
        {"filter": {"includeArchived": True}},
        token=token,
    )

    assert "errors" not in default_result
    assert "errors" not in include_archived_result

    default_ids = {row["id"] for row in default_result["data"]["applicationsSearch"]["applications"]}
    include_ids = {row["id"] for row in include_archived_result["data"]["applicationsSearch"]["applications"]}

    assert "active-app" in default_ids
    assert "archived-app" not in default_ids
    assert "active-app" in include_ids
    assert "archived-app" in include_ids


def test_graphql_applications_search_enforces_strict_preferred_locations(
    test_client: TestClient,
) -> None:
    signup = _signup_user(test_client, full_name="Search Location User", email="search-location@example.com")
    token = signup["token"]
    user_id = signup["user"]["id"]

    preferences_result = _graphql(
        test_client,
        """
        mutation UpdatePreferences(
          $interests: [String!]!
          $applicationsPerDay: Int!
          $locations: [String!]
        ) {
          updatePreferences(
            interests: $interests
            applicationsPerDay: $applicationsPerDay
            locations: $locations
          ) {
            userId
          }
        }
        """,
        {
            "interests": ["platform"],
            "applicationsPerDay": 3,
            "locations": ["Canada"],
        },
        token=token,
    )
    assert "errors" not in preferences_result

    _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="canada-app",
        opportunity_id="canada-job",
        discovered_at_offset_days=0,
        location="Toronto, Canada",
    )
    _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="us-app",
        opportunity_id="us-job",
        discovered_at_offset_days=0,
        location="Austin, United States",
    )
    _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="unknown-location-app",
        opportunity_id="unknown-location-job",
        discovered_at_offset_days=0,
        location=None,
    )

    result = _graphql(
        test_client,
        """
        query Search($filter: ApplicationFilterInput) {
          applicationsSearch(filter: $filter, limit: 25, offset: 0) {
            applications {
              id
            }
            totalCount
          }
        }
        """,
        {"filter": {}},
        token=token,
    )

    assert "errors" not in result
    assert result["data"]["applicationsSearch"]["totalCount"] == 1
    assert result["data"]["applicationsSearch"]["applications"] == [{"id": "canada-app"}]


def test_graphql_apply_selected_applications_deduplicates_ids(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Apply Dedupe",
        email="graph-apply-dedupe@example.com",
    )
    token = signup["token"]
    _seed_profile(test_client, token=token, applications_per_day=3)

    run_result = _graphql(
        test_client,
        """
        mutation RunAgent {
          runAgent {
            id
          }
        }
        """,
        token=token,
    )
    assert "errors" not in run_result
    first_application_id = run_result["data"]["runAgent"][0]["id"]

    apply_result = _graphql(
        test_client,
        """
        mutation ApplySelected($applicationIds: [ID!]!) {
          applySelectedApplications(applicationIds: $applicationIds) {
            runId
            acceptedApplicationIds
            applications {
              id
              status
            }
            skipped {
              applicationId
              reason
            }
          }
        }
        """,
        variables={"applicationIds": [first_application_id, first_application_id]},
        token=token,
    )

    assert "errors" not in apply_result
    payload = apply_result["data"]["applySelectedApplications"]
    assert payload["acceptedApplicationIds"] == [first_application_id]
    assert len(payload["applications"]) == 1
    assert payload["applications"][0]["id"] == first_application_id
    assert payload["applications"][0]["status"] == "applying"
    assert payload["skipped"] == []

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.apply_run_starts == 1
    assert fake_cloud.last_apply_payload is not None
    assert len(fake_cloud.last_apply_payload.jobs) == 1


def test_graphql_apply_selected_applications_accepts_failed_for_retry(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Apply Retry Failed",
        email="graph-apply-retry-failed@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    _seed_profile(test_client, token=token, applications_per_day=3)

    failed = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="failed-retry-app",
        opportunity_id="failed-retry-job",
        discovered_at_offset_days=0,
        status=ApplicationStatus.failed,
    )

    apply_result = _graphql(
        test_client,
        """
        mutation ApplySelected($applicationIds: [ID!]!) {
          applySelectedApplications(applicationIds: $applicationIds) {
            runId
            acceptedApplicationIds
            applications {
              id
              status
            }
            skipped {
              applicationId
              reason
              status
            }
          }
        }
        """,
        variables={"applicationIds": [failed.id]},
        token=token,
    )

    assert "errors" not in apply_result
    payload = apply_result["data"]["applySelectedApplications"]
    assert payload["runId"]
    assert payload["acceptedApplicationIds"] == [failed.id]
    assert payload["applications"] == [{"id": failed.id, "status": "applying"}]
    assert payload["skipped"] == []

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.apply_run_starts == 1
    assert fake_cloud.last_apply_payload is not None
    assert len(fake_cloud.last_apply_payload.jobs) == 1
    assert fake_cloud.last_apply_payload.jobs[0].external_job_id == failed.opportunity.id


def test_graphql_apply_selected_applications_skips_ineligible_but_retries_failed(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Apply Mixed Eligibility",
        email="graph-apply-mixed-eligibility@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    _seed_profile(test_client, token=token, applications_per_day=3)

    failed = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="failed-mixed-app",
        opportunity_id="failed-mixed-job",
        discovered_at_offset_days=0,
        status=ApplicationStatus.failed,
    )
    ineligible = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="applied-mixed-app",
        opportunity_id="applied-mixed-job",
        discovered_at_offset_days=0,
        status=ApplicationStatus.applied,
    )

    apply_result = _graphql(
        test_client,
        """
        mutation ApplySelected($applicationIds: [ID!]!) {
          applySelectedApplications(applicationIds: $applicationIds) {
            runId
            acceptedApplicationIds
            applications {
              id
              status
            }
            skipped {
              applicationId
              reason
              status
            }
          }
        }
        """,
        variables={"applicationIds": [failed.id, ineligible.id]},
        token=token,
    )

    assert "errors" not in apply_result
    payload = apply_result["data"]["applySelectedApplications"]
    assert payload["runId"]
    assert payload["acceptedApplicationIds"] == [failed.id]
    assert payload["applications"] == [{"id": failed.id, "status": "applying"}]
    assert payload["skipped"] == [
        {
            "applicationId": ineligible.id,
            "reason": "ineligible_status",
            "status": "applied",
        }
    ]

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.apply_run_starts == 1
    assert fake_cloud.last_apply_payload is not None
    assert len(fake_cloud.last_apply_payload.jobs) == 1
    assert fake_cloud.last_apply_payload.jobs[0].external_job_id == failed.opportunity.id


def test_graphql_apply_selected_applications_skips_archived_failed_application(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Apply Archived Failed",
        email="graph-apply-archived-failed@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    archived_failed = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-failed-app",
        opportunity_id="archived-failed-job",
        discovered_at_offset_days=45,
        status=ApplicationStatus.failed,
    )

    apply_result = _graphql(
        test_client,
        """
        mutation ApplySelected($applicationIds: [ID!]!) {
          applySelectedApplications(applicationIds: $applicationIds) {
            runId
            acceptedApplicationIds
            applications {
              id
              status
            }
            skipped {
              applicationId
              reason
              status
            }
          }
        }
        """,
        variables={"applicationIds": [archived_failed.id]},
        token=token,
    )

    assert "errors" not in apply_result
    payload = apply_result["data"]["applySelectedApplications"]
    assert payload["runId"] is None
    assert payload["acceptedApplicationIds"] == []
    assert payload["applications"] == []
    assert payload["skipped"] == [
        {
            "applicationId": archived_failed.id,
            "reason": "archived",
            "status": "failed",
        }
    ]

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.apply_run_starts == 0


def test_graphql_mark_application_applied_rejects_archived_application(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Archive",
        email="graph-archive@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    archived = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-graphql-app",
        opportunity_id="archived-graphql-job",
        discovered_at_offset_days=45,
    )

    result = _graphql(
        test_client,
        """
        mutation MarkApplied($applicationId: ID!) {
          markApplicationApplied(applicationId: $applicationId) {
            id
            status
          }
        }
        """,
        {"applicationId": archived.id},
        token=token,
    )
    assert "errors" in result
    assert (
        result["errors"][0]["message"]
        == "Application is archived and cannot be updated"
    )


def test_graphql_mark_application_viewed_rejects_archived_application(
    test_client: TestClient,
) -> None:
    signup = _signup_user(
        test_client,
        full_name="Graph Archive Viewed",
        email="graph-archive-viewed@example.com",
    )
    token = signup["token"]
    user_id = signup["user"]["id"]

    archived = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-graphql-viewed-app",
        opportunity_id="archived-graphql-viewed-job",
        discovered_at_offset_days=45,
    )

    result = _graphql(
        test_client,
        """
        mutation MarkViewed($applicationId: ID!) {
          markApplicationViewed(applicationId: $applicationId) {
            id
            status
          }
        }
        """,
        {"applicationId": archived.id},
        token=token,
    )
    assert "errors" in result
    assert (
        result["errors"][0]["message"]
        == "Application is archived and cannot be updated"
    )


def test_graphql_endpoint_executes_schema_in_threadpool(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"value": False}

    async def fake_run_in_threadpool(fn, *args, **kwargs):
        called["value"] = True
        return fn(*args, **kwargs)

    monkeypatch.setattr("backend.main.run_in_threadpool", fake_run_in_threadpool)
    os.environ.setdefault("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    app = create_app(database_url="sqlite+pysqlite:///:memory:", cloud_client=FakeCloudClient())
    with TestClient(app) as client:
        response = client.post("/graphql", json={"query": "query { __typename }"})
    assert response.status_code == 200
    assert called["value"] is True


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/v1/auth/signup", {"full_name": "Jane", "email": "jane@example.com", "password": "strong-password"}),
        ("POST", "/v1/auth/login", {"email": "jane@example.com", "password": "strong-password"}),
        ("GET", "/v1/auth/me", None),
        ("POST", "/v1/agent/run", None),
        ("GET", "/v1/applications", None),
        ("GET", "/v1/users/user-1/preferences", None),
        ("POST", "/agent/run", None),
        ("GET", "/applications", None),
    ],
)
def test_removed_rest_endpoints_return_not_found(
    test_client: TestClient,
    method: str,
    path: str,
    payload: dict | None,
) -> None:
    response = test_client.request(method=method, url=path, json=payload)
    assert response.status_code == 404


def test_admin_dashboard_renders_html_and_stats(test_client: TestClient) -> None:
    signup = _signup_user(test_client, full_name="Jane Doe", email="admin@example.com")
    _seed_profile(test_client, token=signup["token"], applications_per_day=2)

    run_result = _graphql(
        test_client,
        "mutation RunAgent { runAgent { id } }",
        token=signup["token"],
    )
    assert "errors" not in run_result

    response = test_client.get("/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Agent Apply Admin Dashboard" in response.text
    assert "Total Opportunities" in response.text
    assert "Applied" in response.text
    assert "Notified" in response.text


def test_admin_dashboard_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_ADMIN_DASHBOARD", "false")
    monkeypatch.setenv("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    app = create_app(database_url="sqlite+pysqlite:///:memory:", cloud_client=FakeCloudClient())
    with TestClient(app) as client:
        response = client.get("/admin")
    assert response.status_code == 404


def test_admin_dashboard_secret_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_ADMIN_DASHBOARD", "true")
    monkeypatch.setenv("ADMIN_DASHBOARD_SECRET", "top-secret")
    monkeypatch.setenv("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    app = create_app(database_url="sqlite+pysqlite:///:memory:", cloud_client=FakeCloudClient())
    with TestClient(app) as client:
        forbidden = client.get("/admin")
        allowed = client.get("/admin", headers={"x-admin-secret": "top-secret"})
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
