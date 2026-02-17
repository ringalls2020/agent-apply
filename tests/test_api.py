from collections.abc import Iterator
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.db_models import UserApplicationProfileRow
from backend.main import create_app
from backend.models import (
    CloudApplyRunCreated,
    CloudMatchRunCreated,
    CloudMatchRunStatus,
    MatchRunStatus,
    MatchedJob,
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


def _seed_profile(client: TestClient, *, user_id: str, applications_per_day: int = 3) -> None:
    preferences = {
        "interests": ["ai", "climate"],
        "locations": ["United States"],
        "applications_per_day": applications_per_day,
    }
    resume = {
        "filename": "resume.txt",
        "resume_text": "ML engineer with automation and platform experience.",
    }

    pref_response = client.put(f"/v1/users/{user_id}/preferences", json=preferences)
    resume_response = client.put(f"/v1/users/{user_id}/resume", json=resume)

    assert pref_response.status_code == 200
    assert resume_response.status_code == 200


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
        json={
            "interests": ["cooking", "travel"],
            "locations": ["United States"],
            "applications_per_day": 5,
        },
    )
    assert initial_preferences_response.status_code == 200

    resume_response = test_client.put(
        f"/v1/users/{user_id}/resume",
        json={
            "filename": "resume.txt",
            "resume_text": (
                "Experienced backend engineer with Python, FastAPI, GraphQL, "
                "Kubernetes, and AWS. Interests: MLOps, automation."
            ),
        },
    )
    assert resume_response.status_code == 200

    preferences_response = test_client.get(f"/v1/users/{user_id}/preferences")
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

    _seed_profile(test_client, user_id=user_a["user"]["id"], applications_per_day=2)

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
    _seed_profile(test_client, user_id=user_id, applications_per_day=2)

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
    _seed_profile(test_client, user_id=user["user"]["id"], applications_per_day=2)

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
