from __future__ import annotations

import time

from fastapi.testclient import TestClient

from cloud_automation.main import create_app
from cloud_automation.security import create_hs256_jwt


def _auth_headers() -> dict[str, str]:
    token = create_hs256_jwt(
        payload={"sub": "main-api"},
        secret="dev-cloud-signing-secret",
        issuer="main-api",
        audience="job-intel-api",
        expires_in_seconds=300,
    )
    return {"authorization": f"Bearer {token}"}


def _poll_until_terminal(client: TestClient, path: str) -> dict:
    for _ in range(25):
        response = client.get(path, headers=_auth_headers())
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed", "partial"}:
            return body
        time.sleep(0.05)
    raise AssertionError("Run did not reach terminal state in time")


def test_cloud_match_and_apply_run_lifecycle() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")

    with TestClient(app) as client:
        run_discovery = client.post("/v1/discovery/run", headers=_auth_headers())
        assert run_discovery.status_code == 200

        search = client.get(
            "/v1/jobs/search?q=engineer&location=United%20States&limit=5",
            headers=_auth_headers(),
        )
        assert search.status_code == 200
        jobs = search.json()["jobs"]
        assert len(jobs) >= 1

        match_start = client.post(
            "/v1/match-runs",
            headers=_auth_headers(),
            json={
                "user_ref": "user-1",
                "resume_text": "Backend python engineer",
                "preferences": {"interests": ["backend", "python"]},
                "limit": 5,
                "location": "United States",
            },
        )
        assert match_start.status_code == 200
        match_run_id = match_start.json()["run_id"]

        match_status = _poll_until_terminal(client, f"/v1/match-runs/{match_run_id}")
        assert match_status["status"] == "completed"

        apply_job = {
            "external_job_id": jobs[0]["id"],
            "title": jobs[0]["title"],
            "company": jobs[0]["company"],
            "apply_url": jobs[0]["apply_url"],
        }

        apply_start = client.post(
            "/v1/apply-runs",
            headers=_auth_headers(),
            json={
                "user_ref": "user-1",
                "jobs": [apply_job],
                "profile_payload": {"full_name": "Jane Doe"},
                "daily_cap": 25,
            },
        )
        assert apply_start.status_code == 200
        apply_run_id = apply_start.json()["run_id"]

        apply_status = _poll_until_terminal(client, f"/v1/apply-runs/{apply_run_id}")
        assert apply_status["status"] == "completed"
        assert len(apply_status["attempts"]) == 1
