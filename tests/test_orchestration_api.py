from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.models import (
    ApplyAttemptResult,
    ApplyAttemptStatus,
    CloudApplyRunCreated,
    CloudApplyRunStatus,
    CloudMatchRunCreated,
    CloudMatchRunStatus,
    MatchedJob,
    MatchRunStatus,
)
from backend.security import create_body_signature, create_hs256_jwt


class FakeCloudClient:
    def __init__(self) -> None:
        self.match_run_id = "match-run-1"
        self.apply_run_id = "apply-run-1"

    def start_match_run(self, payload):
        return CloudMatchRunCreated(
            run_id=self.match_run_id,
            status=MatchRunStatus.queued,
            status_url=f"/v1/match-runs/{self.match_run_id}",
        )

    def get_match_run(self, run_id: str):
        assert run_id == self.match_run_id
        return CloudMatchRunStatus(
            run_id=run_id,
            status=MatchRunStatus.completed,
            matches=[
                MatchedJob(
                    external_job_id="job-1",
                    title="Backend Engineer",
                    company="Acme",
                    location="United States",
                    apply_url="https://example.com/jobs/1",
                    source="greenhouse",
                    reason="Strong backend overlap",
                    score=0.89,
                )
            ],
        )

    def start_apply_run(self, payload):
        return CloudApplyRunCreated(
            run_id=self.apply_run_id,
            status=MatchRunStatus.queued,
            status_url=f"/v1/apply-runs/{self.apply_run_id}",
        )

    def get_apply_run(self, run_id: str):
        assert run_id == self.apply_run_id
        return CloudApplyRunStatus(
            run_id=run_id,
            status=MatchRunStatus.completed,
            attempts=[
                ApplyAttemptResult(
                    attempt_id="attempt-1",
                    external_job_id="job-1",
                    job_url="https://example.com/jobs/1",
                    status=ApplyAttemptStatus.succeeded,
                )
            ],
        )


@pytest.fixture
def test_client() -> Iterator[TestClient]:
    app = create_app(database_url="sqlite+pysqlite:///:memory:", cloud_client=FakeCloudClient())
    with TestClient(app) as client:
        yield client


def _auth_headers(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def _seed_user(client: TestClient) -> tuple[str, str]:
    signup = client.post(
        "/v1/auth/signup",
        json={
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "password": "strong-password",
        },
    )
    assert signup.status_code == 201
    body = signup.json()
    user_id = body["user"]["id"]
    token = body["token"]

    pref_payload = {
        "interests": ["backend", "python"],
        "locations": ["United States"],
        "seniority": "mid",
        "applications_per_day": 25,
    }
    resume_payload = {
        "filename": "resume.txt",
        "resume_text": "Python backend engineer with FastAPI and SQLAlchemy.",
    }

    headers = _auth_headers(token)
    assert client.put(f"/v1/users/{user_id}/preferences", json=pref_payload, headers=headers).status_code == 200
    assert client.put(f"/v1/users/{user_id}/resume", json=resume_payload, headers=headers).status_code == 200
    return user_id, token


def test_match_run_flow_round_trip(test_client: TestClient) -> None:
    user_id, token = _seed_user(test_client)

    start_response = test_client.post(
        f"/v1/users/{user_id}/match-runs",
        json={"limit": 10},
        headers=_auth_headers(token),
    )
    assert start_response.status_code == 200
    assert start_response.json()["run_id"] == "match-run-1"

    status_response = test_client.get(
        f"/v1/users/{user_id}/match-runs/match-run-1",
        headers=_auth_headers(token),
    )
    assert status_response.status_code == 200
    body = status_response.json()
    assert body["status"] == "completed"
    assert len(body["results"]) == 1
    assert body["results"][0]["company"] == "Acme"


def test_apply_run_daily_cap_is_enforced(test_client: TestClient) -> None:
    user_id, token = _seed_user(test_client)
    test_client.put(
        f"/v1/users/{user_id}/preferences",
        json={
            "interests": ["backend"],
            "locations": ["United States"],
            "seniority": "mid",
            "applications_per_day": 1,
        },
        headers=_auth_headers(token),
    )

    response = test_client.post(
        f"/v1/users/{user_id}/apply-runs",
        json={
            "jobs": [
                {
                    "external_job_id": "job-1",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "apply_url": "https://example.com/jobs/1",
                },
                {
                    "external_job_id": "job-2",
                    "title": "Platform Engineer",
                    "company": "Acme",
                    "apply_url": "https://example.com/jobs/2",
                },
            ]
        },
        headers=_auth_headers(token),
    )

    assert response.status_code == 400
    assert "Daily cap exceeded" in response.json()["detail"]


def test_signed_apply_callback_is_idempotent(test_client: TestClient) -> None:
    user_id, _token = _seed_user(test_client)

    payload = {
        "event_type": "apply.attempt.updated",
        "idempotency_key": "idem-1",
        "run_id": "apply-run-1",
        "user_ref": user_id,
        "attempt": {
            "attempt_id": "attempt-callback-1",
            "external_job_id": "job-1",
            "job_url": "https://example.com/jobs/1",
            "status": "succeeded",
            "artifacts": [],
        },
        "emitted_at": "2026-02-17T00:00:00Z",
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    timestamp = str(int(time.time()))
    nonce = "nonce-1"
    signature_secret = "dev-cloud-signing-secret"
    signature = create_body_signature(
        body=body,
        timestamp=timestamp,
        nonce=nonce,
        secret=signature_secret,
    )
    token = create_hs256_jwt(
        payload={"sub": "job-intel-api"},
        secret=signature_secret,
        issuer="job-intel-api",
        audience="main-api",
        expires_in_seconds=300,
    )

    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "x-cloud-timestamp": timestamp,
        "x-cloud-nonce": nonce,
        "x-cloud-signature": signature,
        "x-idempotency-key": "idem-1",
    }

    first = test_client.post(
        "/internal/cloud/callbacks/apply-result",
        content=body,
        headers=headers,
    )
    second = test_client.post(
        "/internal/cloud/callbacks/apply-result",
        content=body,
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is True
