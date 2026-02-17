from collections.abc import Iterator
from datetime import timedelta
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from common.time import utc_now
from backend.db_models import ApplicationRecordRow, UserApplicationProfileRow
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

    def run_discovery_now(self) -> dict[str, bool]:
        return {"accepted": True}

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
            status_url=f"/v1/match-runs/{run_id}",
        )

    def get_match_run(self, run_id: str) -> CloudMatchRunStatus:
        matches = self._runs.get(run_id, [])
        return CloudMatchRunStatus(
            run_id=run_id,
            status=MatchRunStatus.completed,
            matches=matches,
        )

    def start_apply_run(self, payload) -> CloudApplyRunCreated:
        del payload
        self.apply_run_starts += 1
        run_id = f"apply-run-{self.apply_run_starts}"
        return CloudApplyRunCreated(
            run_id=run_id,
            status=MatchRunStatus.queued,
            status_url=f"/v1/apply-runs/{run_id}",
        )


class StableJobCloudClient(FakeCloudClient):
    def start_match_run(self, payload) -> CloudMatchRunCreated:
        del payload
        run_id = f"stable-match-run-{self._next_run}"
        self._next_run += 1
        self._runs[run_id] = [
            MatchedJob(
                external_job_id="stable-job-1",
                title="Stable Backend Engineer",
                company="Stable Corp",
                location="United States",
                apply_url="https://jobs.live-board.test/stable-1",
                source="greenhouse",
                reason="Stable cloud fixture for anchor tests",
                score=0.95,
                posted_at=None,
            )
        ]
        return CloudMatchRunCreated(
            run_id=run_id,
            status=MatchRunStatus.queued,
            status_url=f"/v1/match-runs/{run_id}",
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




def _graphql(client: TestClient, query: str, variables: dict | None = None, token: str | None = None):
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

def _auth_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _signup_user(
    client: TestClient,
    *,
    full_name: str,
    email: str,
    password: str = "strong-password",
) -> dict:
    response = client.post(
        "/v1/auth/signup",
        json={
            "full_name": full_name,
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 201
    return response.json()


def _seed_profile(
    client: TestClient,
    *,
    user_id: str,
    token: str,
    applications_per_day: int = 3,
) -> None:
    preferences = {
        "interests": ["ai", "climate"],
        "locations": ["United States"],
        "applications_per_day": applications_per_day,
    }
    resume = {
        "filename": "resume.txt",
        "resume_text": "ML engineer with automation and platform experience.",
    }

    headers = _auth_headers(token)
    pref_response = client.put(f"/v1/users/{user_id}/preferences", json=preferences, headers=headers)
    resume_response = client.put(f"/v1/users/{user_id}/resume", json=resume, headers=headers)

    assert pref_response.status_code == 200
    assert resume_response.status_code == 200


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


def test_health_endpoint_reuses_incoming_request_id_header(
    test_client: TestClient,
) -> None:
    response = test_client.get("/health", headers={"x-request-id": "req-123"})

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-123"


def test_signup_login_and_me_flow(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane@example.com",
    )

    signup_token = signup_body["token"]
    me_response = test_client.get("/v1/auth/me", headers=_auth_headers(signup_token))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "jane@example.com"
    assert me_response.json()["autosubmit_enabled"] is False

    login_response = test_client.post(
        "/v1/auth/login",
        json={"email": "jane@example.com", "password": "strong-password"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["user"]["id"] == signup_body["user"]["id"]


def test_login_rejects_invalid_credentials(test_client: TestClient) -> None:
    _signup_user(test_client, full_name="Jane Doe", email="jane@example.com")

    login_response = test_client.post(
        "/v1/auth/login",
        json={"email": "jane@example.com", "password": "wrong-password"},
    )
    assert login_response.status_code == 401
    assert login_response.json()["detail"] == "Invalid credentials."


def test_user_routes_require_auth(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Auth", email="auth-required@example.com")
    response = test_client.get(f"/v1/users/{user['user']['id']}")
    assert response.status_code == 401


def test_user_routes_require_subject_match(test_client: TestClient) -> None:
    owner = _signup_user(test_client, full_name="Jane Owner", email="owner-auth@example.com")
    other = _signup_user(test_client, full_name="Jane Other", email="other-auth@example.com")
    response = test_client.get(
        f"/v1/users/{owner['user']['id']}",
        headers=_auth_headers(other["token"]),
    )
    assert response.status_code == 403


def test_agent_run_requires_resume(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane@example.com",
    )

    response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(signup_body["token"]),
    )
    assert response.status_code == 400
    assert "User resume not found" in response.json()["detail"]


def test_resume_upload_sanitizes_nul_bytes(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane-resume@example.com",
    )
    user_id = signup_body["user"]["id"]

    response = test_client.put(
        f"/v1/users/{user_id}/resume",
        headers=_auth_headers(signup_body["token"]),
        json={
            "filename": "resume.pdf",
            "resume_text": "header\x00body\x00tail",
        },
    )

    assert response.status_code == 200
    assert "\x00" not in response.json()["resume_text"]
    assert response.json()["resume_text"] == "headerbodytail"


def test_resume_upload_updates_preferences_from_resume_content(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane-skills@example.com",
    )
    user_id = signup_body["user"]["id"]

    initial_preferences_response = test_client.put(
        f"/v1/users/{user_id}/preferences",
        headers=_auth_headers(signup_body["token"]),
        json={
            "interests": ["cooking", "travel"],
            "locations": ["United States"],
            "applications_per_day": 5,
        },
    )
    assert initial_preferences_response.status_code == 200

    resume_response = test_client.put(
        f"/v1/users/{user_id}/resume",
        headers=_auth_headers(signup_body["token"]),
        json={
            "filename": "resume.txt",
            "resume_text": (
                "Experienced backend engineer with Python, FastAPI, GraphQL, "
                "Kubernetes, and AWS. Interests: MLOps, automation."
            ),
        },
    )
    assert resume_response.status_code == 200

    preferences_response = test_client.get(
        f"/v1/users/{user_id}/preferences",
        headers=_auth_headers(signup_body["token"]),
    )
    assert preferences_response.status_code == 200
    preferences = preferences_response.json()
    interests = preferences["interests"]

    assert "python" in interests
    assert "fastapi" in interests
    assert "graphql" in interests
    assert "kubernetes" in interests
    assert "aws" in interests
    assert "mlops" in interests
    assert "automation" in interests
    assert "cooking" not in interests
    assert "travel" not in interests
    assert preferences["applications_per_day"] == 5
    assert preferences["locations"] == ["United States"]


def test_resume_upload_rejects_empty_after_sanitization(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Jane Doe",
        email="jane-empty@example.com",
    )
    user_id = signup_body["user"]["id"]

    response = test_client.put(
        f"/v1/users/{user_id}/resume",
        headers=_auth_headers(signup_body["token"]),
        json={
            "filename": "resume.pdf",
            "resume_text": "\u0000\u0000\u0000",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Resume text is empty after sanitization"


def test_agent_run_and_list_applications_are_user_scoped(test_client: TestClient) -> None:
    user_a = _signup_user(test_client, full_name="Jane A", email="a@example.com")
    user_b = _signup_user(test_client, full_name="Jane B", email="b@example.com")

    _seed_profile(test_client, user_id=user_a["user"]["id"], token=user_a["token"], applications_per_day=2)

    run_response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user_a["token"]),
    )
    assert run_response.status_code == 200
    assert len(run_response.json()["applications"]) == 2
    assert all(item["status"] == "review" for item in run_response.json()["applications"])

    list_a = test_client.get("/v1/applications", headers=_auth_headers(user_a["token"]))
    list_b = test_client.get("/v1/applications", headers=_auth_headers(user_b["token"]))

    assert list_a.status_code == 200
    assert list_b.status_code == 200
    assert len(list_a.json()["applications"]) == 2
    assert len(list_b.json()["applications"]) == 0


def test_profile_round_trip_and_owner_auth(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Profile", email="profile@example.com")
    other = _signup_user(test_client, full_name="Jane Other", email="profile-other@example.com")
    user_id = user["user"]["id"]

    put_response = test_client.put(
        f"/v1/users/{user_id}/profile",
        headers=_auth_headers(user["token"]),
        json={
            "autosubmit_enabled": True,
            "work_authorization": "US Citizen",
            "requires_sponsorship": False,
            "willing_to_relocate": True,
            "years_experience": 6,
            "writing_voice": "concise",
            "custom_answers": [
                {"question_key": "favorite_stack", "answer": "python-fastapi-postgres"}
            ],
            "sensitive": {
                "gender": "decline_to_answer",
                "race_ethnicity": "decline_to_answer",
                "veteran_status": "not_a_protected_veteran",
                "disability_status": "decline_to_answer",
            },
        },
    )
    assert put_response.status_code == 200
    assert put_response.json()["autosubmit_enabled"] is True
    assert put_response.json()["custom_answers"][0]["question_key"] == "favorite_stack"
    with test_client.app.state.main_store._session_factory() as session:
        stored = session.scalar(
            select(UserApplicationProfileRow).where(UserApplicationProfileRow.user_id == user_id)
        )
    assert stored is not None
    assert stored.veteran_status_encrypted is not None
    assert stored.veteran_status_encrypted != "not_a_protected_veteran"

    get_response = test_client.get(
        f"/v1/users/{user_id}/profile",
        headers=_auth_headers(user["token"]),
    )
    assert get_response.status_code == 200
    assert get_response.json()["sensitive"]["veteran_status"] == "not_a_protected_veteran"

    forbidden = test_client.get(
        f"/v1/users/{user_id}/profile",
        headers=_auth_headers(other["token"]),
    )
    assert forbidden.status_code == 403


def test_agent_run_with_autosubmit_starts_apply_run(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Auto", email="auto@example.com")
    user_id = user["user"]["id"]
    _seed_profile(test_client, user_id=user_id, token=user["token"], applications_per_day=2)

    profile_response = test_client.put(
        f"/v1/users/{user_id}/profile",
        headers=_auth_headers(user["token"]),
        json={"autosubmit_enabled": True},
    )
    assert profile_response.status_code == 200

    run_response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user["token"]),
    )
    assert run_response.status_code == 200
    body = run_response.json()
    assert len(body["applications"]) == 2
    assert all(item["status"] == "applying" for item in body["applications"])

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.apply_run_starts == 1


def test_agent_run_preserves_first_seen_anchor_when_match_posted_at_missing() -> None:
    os.environ.setdefault("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    stable_cloud = StableJobCloudClient()
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        cloud_client=stable_cloud,
    )
    with TestClient(app) as client:
        user = _signup_user(client, full_name="Jane Anchor", email="anchor@example.com")
        user_id = user["user"]["id"]
        token = user["token"]
        _seed_profile(client, user_id=user_id, token=token, applications_per_day=2)

        first_run = client.post("/v1/agent/run", headers=_auth_headers(token))
        assert first_run.status_code == 200
        application_id = first_run.json()["applications"][0]["id"]

        with app.state.main_store._session_factory() as session:
            row = session.get(ApplicationRecordRow, application_id)
            assert row is not None
            row.opportunity_discovered_at = utc_now() - timedelta(days=40)
            session.commit()

        second_run = client.post("/v1/agent/run", headers=_auth_headers(token))
        assert second_run.status_code == 200
        second_app = second_run.json()["applications"][0]
        assert second_app["is_archived"] is True

        default_list = client.get("/v1/applications", headers=_auth_headers(token))
        assert default_list.status_code == 200
        assert default_list.json()["applications"] == []

        archived_list = client.get(
            "/v1/applications?include_archived=true",
            headers=_auth_headers(token),
        )
        assert archived_list.status_code == 200
        assert len(archived_list.json()["applications"]) == 1
        assert archived_list.json()["applications"][0]["is_archived"] is True


def test_mark_viewed_transitions_review_and_is_idempotent(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Viewed", email="viewed@example.com")
    _seed_profile(test_client, user_id=user["user"]["id"], token=user["token"], applications_per_day=2)

    run_response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user["token"]),
    )
    assert run_response.status_code == 200
    application_id = run_response.json()["applications"][0]["id"]

    viewed_response = test_client.post(
        f"/v1/applications/{application_id}/mark-viewed",
        headers=_auth_headers(user["token"]),
    )
    assert viewed_response.status_code == 200
    assert viewed_response.json()["status"] == "viewed"

    viewed_again_response = test_client.post(
        f"/v1/applications/{application_id}/mark-viewed",
        headers=_auth_headers(user["token"]),
    )
    assert viewed_again_response.status_code == 200
    assert viewed_again_response.json()["status"] == "viewed"


def test_bulk_apply_accepts_review_and_viewed_statuses(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Bulk", email="bulk@example.com")
    _seed_profile(test_client, user_id=user["user"]["id"], token=user["token"], applications_per_day=2)

    run_response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user["token"]),
    )
    assert run_response.status_code == 200
    applications = run_response.json()["applications"]
    first_application_id = applications[0]["id"]
    second_application_id = applications[1]["id"]

    mark_viewed_response = test_client.post(
        f"/v1/applications/{first_application_id}/mark-viewed",
        headers=_auth_headers(user["token"]),
    )
    assert mark_viewed_response.status_code == 200
    assert mark_viewed_response.json()["status"] == "viewed"

    apply_response = test_client.post(
        "/v1/applications/apply",
        headers=_auth_headers(user["token"]),
        json={"application_ids": [first_application_id, second_application_id]},
    )
    assert apply_response.status_code == 200
    body = apply_response.json()
    assert body["run_id"]
    assert body["accepted_application_ids"] == [first_application_id, second_application_id]
    assert body["skipped"] == []
    assert all(item["status"] == "applying" for item in body["applications"])

    fake_cloud = test_client.app.state.test_fake_cloud_client
    assert fake_cloud.apply_run_starts == 1

    mark_viewed_after_applying = test_client.post(
        f"/v1/applications/{first_application_id}/mark-viewed",
        headers=_auth_headers(user["token"]),
    )
    assert mark_viewed_after_applying.status_code == 200
    assert mark_viewed_after_applying.json()["status"] == "applying"


def test_applications_hide_archived_by_default_and_include_with_toggle(
    test_client: TestClient,
) -> None:
    user = _signup_user(test_client, full_name="Jane Archive", email="archive@example.com")
    user_id = user["user"]["id"]
    token = user["token"]

    fresh = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="fresh-app",
        opportunity_id="job-fresh",
        discovered_at_offset_days=2,
    )
    archived = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-app",
        opportunity_id="job-archived",
        discovered_at_offset_days=30,
    )

    hidden_default = test_client.get("/v1/applications", headers=_auth_headers(token))
    assert hidden_default.status_code == 200
    hidden_apps = hidden_default.json()["applications"]
    assert [item["id"] for item in hidden_apps] == [fresh.id]
    assert hidden_apps[0]["is_archived"] is False

    listed_with_archive = test_client.get(
        "/v1/applications?include_archived=true",
        headers=_auth_headers(token),
    )
    assert listed_with_archive.status_code == 200
    listed_ids = {item["id"] for item in listed_with_archive.json()["applications"]}
    assert listed_ids == {fresh.id, archived.id}
    archived_payload = next(
        item for item in listed_with_archive.json()["applications"] if item["id"] == archived.id
    )
    assert archived_payload["is_archived"] is True

    search_default = test_client.get("/v1/applications/search", headers=_auth_headers(token))
    assert search_default.status_code == 200
    assert [item["id"] for item in search_default.json()["applications"]] == [fresh.id]

    search_with_archive = test_client.get(
        "/v1/applications/search?include_archived=true",
        headers=_auth_headers(token),
    )
    assert search_with_archive.status_code == 200
    search_ids = {item["id"] for item in search_with_archive.json()["applications"]}
    assert search_ids == {fresh.id, archived.id}


def test_bulk_apply_skips_archived_applications(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Archived Apply", email="archived-apply@example.com")
    user_id = user["user"]["id"]
    token = user["token"]
    _seed_profile(test_client, user_id=user_id, token=token, applications_per_day=5)

    fresh = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="fresh-bulk-app",
        opportunity_id="job-fresh-bulk",
        discovered_at_offset_days=1,
    )
    archived = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-bulk-app",
        opportunity_id="job-archived-bulk",
        discovered_at_offset_days=40,
    )

    apply_response = test_client.post(
        "/v1/applications/apply",
        headers=_auth_headers(token),
        json={"application_ids": [archived.id, fresh.id]},
    )
    assert apply_response.status_code == 200
    body = apply_response.json()
    assert body["accepted_application_ids"] == [fresh.id]
    assert body["run_id"]
    assert body["skipped"] == [
        {
            "application_id": archived.id,
            "reason": "archived",
            "status": "review",
        }
    ]


def test_mark_actions_reject_archived_application(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Archived Mark", email="archived-mark@example.com")
    user_id = user["user"]["id"]
    token = user["token"]

    archived = _seed_application_record(
        test_client,
        user_id=user_id,
        app_id="archived-mark-app",
        opportunity_id="job-archived-mark",
        discovered_at_offset_days=50,
    )

    mark_viewed = test_client.post(
        f"/v1/applications/{archived.id}/mark-viewed",
        headers=_auth_headers(token),
    )
    assert mark_viewed.status_code == 400
    assert mark_viewed.json()["detail"] == "Application is archived and cannot be updated"

    mark_applied = test_client.post(
        f"/v1/applications/{archived.id}/mark-applied",
        headers=_auth_headers(token),
    )
    assert mark_applied.status_code == 400
    assert mark_applied.json()["detail"] == "Application is archived and cannot be updated"


def test_mark_viewed_is_user_scoped(test_client: TestClient) -> None:
    owner = _signup_user(test_client, full_name="Jane Owner", email="owner@example.com")
    other = _signup_user(test_client, full_name="Jane Other", email="other-owner@example.com")
    _seed_profile(test_client, user_id=owner["user"]["id"], token=owner["token"], applications_per_day=1)

    run_response = test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(owner["token"]),
    )
    assert run_response.status_code == 200
    application_id = run_response.json()["applications"][0]["id"]

    forbidden_response = test_client.post(
        f"/v1/applications/{application_id}/mark-viewed",
        headers=_auth_headers(other["token"]),
    )
    assert forbidden_response.status_code == 404


def test_legacy_application_endpoints_return_gone(test_client: TestClient) -> None:
    response_run = test_client.post(
        "/agent/run",
        json={
            "profile": {
                "full_name": "Jane Doe",
                "email": "jane@example.com",
                "resume_text": "any",
                "interests": ["ai"],
            },
            "max_opportunities": 1,
        },
    )
    response_list = test_client.get("/applications")

    assert response_run.status_code == 410
    assert response_list.status_code == 410


def test_admin_dashboard_renders_html_and_stats(test_client: TestClient) -> None:
    user = _signup_user(test_client, full_name="Jane Doe", email="jane@example.com")
    _seed_profile(test_client, user_id=user["user"]["id"], token=user["token"], applications_per_day=2)

    test_client.post(
        "/v1/agent/run",
        headers=_auth_headers(user["token"]),
    )

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


def test_graphql_signup_and_me_flow(test_client: TestClient) -> None:
    signup = _graphql(
        test_client,
        """
        mutation Signup($fullName: String!, $email: String!, $password: String!) {
          signup(fullName: $fullName, email: $email, password: $password) {
            token
            user {
              id
              email
              fullName
            }
          }
        }
        """,
        {
            "fullName": "Graph QL",
            "email": "graphql@example.com",
            "password": "strong-password",
        },
    )
    assert "errors" not in signup
    token = signup["data"]["signup"]["token"]

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
    assert me["data"]["me"]["email"] == "graphql@example.com"


def test_graphql_run_agent_mutation_returns_applications(test_client: TestClient) -> None:
    signup_body = _signup_user(
        test_client,
        full_name="Graph Runner",
        email="graph-runner@example.com",
    )
    token = signup_body["token"]
    user_id = signup_body["user"]["id"]
    _seed_profile(test_client, user_id=user_id, token=token, applications_per_day=2)

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
