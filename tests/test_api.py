import base64
from collections.abc import Iterator
from datetime import timedelta
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from common.time import utc_now
from backend.db_models import ExternalRunRefRow, ResumeRow
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
        matches: list[MatchedJob] = []
        for idx in range(payload.limit):
            keyword = str(interests[idx % len(interests)]).title()
            matches.append(
                MatchedJob(
                    external_job_id=f"{run_id}-job-{idx + 1}",
                    title=f"{keyword} Engineer {idx + 1}",
                    company=f"Live Board {idx + 1}",
                    location=payload.location or "United States",
                    apply_url=f"https://jobs.live-board.test/{idx + 1}",
                    source="greenhouse",
                    reason="Synthetic cloud fixture for API tests",
                    score=max(0.1, 1.0 - (idx * 0.05)),
                )
            )

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
    include_application_profile: bool = False,
) -> None:
    preferences_result = _graphql(
        client,
        """
        mutation UpdatePreferences($interests: [String!]!, $applicationsPerDay: Int!) {
          updatePreferences(interests: $interests, applicationsPerDay: $applicationsPerDay) {
            userId
            interests
            applicationsPerDay
          }
        }
        """,
        {
            "interests": ["ai", "climate"],
            "applicationsPerDay": applications_per_day,
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
    status: ApplicationStatus = ApplicationStatus.review,
) -> ApplicationRecord:
    record = ApplicationRecord(
        id=app_id,
        opportunity=Opportunity(
            id=opportunity_id,
            title="Platform Engineer",
            company="Acme",
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
            applicationsPerDay
          }
        }
        """,
        token=token,
    )
    assert "errors" not in me
    assert me["data"]["me"]["email"] == "jane@example.com"

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
