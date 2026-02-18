from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app
from backend.models import (
    ApplicationRecord,
    ApplicationStatus,
    Opportunity,
)
from backend.security import create_body_signature, create_hs256_jwt


class FakeCloudClient:
    def run_discovery_now(self) -> dict[str, bool]:
        return {"accepted": True}


@pytest.fixture
def test_client() -> Iterator[TestClient]:
    os.environ.setdefault("USER_PROFILE_ENCRYPTION_KEY", "test-profile-encryption-key")
    app = create_app(database_url="sqlite+pysqlite:///:memory:", cloud_client=FakeCloudClient())
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


def _post_signed_apply_callback(client: TestClient, payload: dict, *, idempotency_key: str):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = f"nonce-{idempotency_key}"
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
        "x-idempotency-key": idempotency_key,
    }
    return client.post(
        "/internal/cloud/callbacks/apply-result",
        content=body,
        headers=headers,
    )


def _seed_user(client: TestClient, *, email: str) -> tuple[str, str]:
    signup = _graphql(
        client,
        """
        mutation Signup($fullName: String!, $email: String!, $password: String!) {
          signup(fullName: $fullName, email: $email, password: $password) {
            token
            user {
              id
            }
          }
        }
        """,
        {
            "fullName": "Jane Doe",
            "email": email,
            "password": "strong-password",
        },
    )
    assert "errors" not in signup
    user_id = signup["data"]["signup"]["user"]["id"]
    token = signup["data"]["signup"]["token"]
    return user_id, token


def _seed_application_record(client: TestClient, *, user_id: str, status: ApplicationStatus = ApplicationStatus.review) -> None:
    client.app.state.store.upsert_for_user(
        user_id,
        ApplicationRecord(
            id="app-job-1",
            opportunity=Opportunity(
                id="job-1",
                title="Backend Engineer",
                company="Acme",
                url="https://example.com/jobs/1",
                reason="Seeded for callback mapping test",
            ),
            status=status,
        ),
    )


def _application_status(client: TestClient, *, token: str, application_id: str) -> str:
    result = _graphql(
        client,
        """
        query Applications($includeArchived: Boolean!) {
          applications(includeArchived: $includeArchived) {
            id
            status
          }
        }
        """,
        {"includeArchived": True},
        token=token,
    )
    assert "errors" not in result
    row = next(
        item
        for item in result["data"]["applications"]
        if item["id"] == application_id
    )
    return row["status"]


def test_signed_apply_callback_is_idempotent(test_client: TestClient) -> None:
    user_id, _token = _seed_user(test_client, email="idem@example.com")

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
    first = _post_signed_apply_callback(test_client, payload, idempotency_key="idem-1")
    second = _post_signed_apply_callback(test_client, payload, idempotency_key="idem-1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["accepted"] is True
    assert second.json()["accepted"] is True


def test_blocked_manual_review_timeout_maps_application_to_review(
    test_client: TestClient,
) -> None:
    user_id, token = _seed_user(test_client, email="manual-review@example.com")
    _seed_application_record(test_client, user_id=user_id)

    payload = {
        "event_type": "apply.attempt.updated",
        "idempotency_key": "idem-manual-review",
        "run_id": "apply-run-manual-review",
        "user_ref": user_id,
        "attempt": {
            "attempt_id": "attempt-manual-review",
            "external_job_id": "job-1",
            "job_url": "https://example.com/jobs/1",
            "status": "blocked",
            "failure_code": "manual_review_timeout",
            "failure_reason": "Manual submit not detected in time",
            "artifacts": [],
        },
        "emitted_at": "2026-02-17T00:00:00Z",
    }

    callback = _post_signed_apply_callback(
        test_client,
        payload,
        idempotency_key="idem-manual-review",
    )
    assert callback.status_code == 200
    assert callback.json()["accepted"] is True

    assert (
        _application_status(test_client, token=token, application_id="app-job-1")
        == "review"
    )


def test_blocked_non_timeout_reason_maps_application_to_failed(
    test_client: TestClient,
) -> None:
    user_id, token = _seed_user(test_client, email="site-blocked@example.com")
    _seed_application_record(test_client, user_id=user_id)

    payload = {
        "event_type": "apply.attempt.updated",
        "idempotency_key": "idem-site-blocked",
        "run_id": "apply-run-site-blocked",
        "user_ref": user_id,
        "attempt": {
            "attempt_id": "attempt-site-blocked",
            "external_job_id": "job-1",
            "job_url": "https://example.com/jobs/1",
            "status": "blocked",
            "failure_code": "site_blocked",
            "failure_reason": "Site blocked automation",
            "artifacts": [],
        },
        "emitted_at": "2026-02-17T00:00:00Z",
    }

    callback = _post_signed_apply_callback(
        test_client,
        payload,
        idempotency_key="idem-site-blocked",
    )
    assert callback.status_code == 200
    assert callback.json()["accepted"] is True

    assert (
        _application_status(test_client, token=token, application_id="app-job-1")
        == "failed"
    )
