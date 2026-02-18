from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import cloud_automation.services_legacy as cloud_services_legacy
from common.time import utc_now
from cloud_automation.db_models import DiscoveryRefreshRequestRow, NormalizedJobRow
from cloud_automation.main import create_app
from cloud_automation.models import ApplyAttemptCallbackPayload, ApplyAttemptRecord, ApplyAttemptStatus
from cloud_automation.security import create_hs256_jwt
from cloud_automation.services import CallbackEmitter, PlaywrightApplyExecutor, SimulatedApplyExecutor


def _auth_headers() -> dict[str, str]:
    token = create_hs256_jwt(
        payload={"sub": "main-api"},
        secret="dev-cloud-signing-secret",
        issuer="main-api",
        audience="job-intel-api",
        expires_in_seconds=300,
    )
    return {"authorization": f"Bearer {token}"}


def _seed_manifest_headers(*, issuer: str = "job-intel-api") -> dict[str, str]:
    token = create_hs256_jwt(
        payload={"sub": issuer},
        secret="dev-cloud-signing-secret",
        issuer=issuer,
        audience="job-intel-api",
        expires_in_seconds=300,
    )
    return {"authorization": f"Bearer {token}"}


def test_discovery_kick_enqueues_refresh_request() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as client:
        response = client.post("/v1/discovery/kick", headers=_auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        request_id = body["request_id"]
        assert isinstance(request_id, str) and request_id

        with app.state.store._session_factory() as session:
            row = session.get(DiscoveryRefreshRequestRow, request_id)
            assert row is not None
            assert row.status == "queued"
            assert row.requested_by == "main-api"


def test_internal_seed_manifest_endpoints_require_service_jwt() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as client:
        missing_auth = client.get("/internal/seed-manifests/companies.json")
        assert missing_auth.status_code == 401

        wrong_issuer = client.get(
            "/internal/seed-manifests/companies.json",
            headers=_seed_manifest_headers(issuer="unauthorized-service"),
        )
        assert wrong_issuer.status_code == 401


def test_internal_seed_manifest_endpoints_return_json_and_csv() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as client:
        app.state.store.replace_seed_manifest_entries(
            entries=[
                (
                    "Acme",
                    "https://acme.example/careers",
                    "https://remoteintech.company/companies/",
                ),
                (
                    "Beta",
                    "https://jobs.lever.co/beta",
                    "https://remoteintech.company/browse/worldwide/",
                ),
            ]
        )
        json_response = client.get(
            "/internal/seed-manifests/companies.json",
            headers=_seed_manifest_headers(),
        )
        assert json_response.status_code == 200
        payload = json_response.json()
        assert len(payload) == 2
        assert payload[0]["careers_url"] == "https://acme.example/careers"

        csv_response = client.get(
            "/internal/seed-manifests/companies.csv",
            headers=_seed_manifest_headers(),
        )
        assert csv_response.status_code == 200
        assert "text/csv" in csv_response.headers["content-type"]
        csv_body = csv_response.text
        assert "company,careers_url,source_page_url" in csv_body
        assert "Acme,https://acme.example/careers,https://remoteintech.company/companies/" in csv_body


def test_cloud_match_and_apply_run_lifecycle() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")

    with TestClient(app) as client:
        run_discovery = client.post("/v1/discovery/run", headers=_auth_headers())
        assert run_discovery.status_code == 200

        # Token-first discovery requires external seed manifests; insert one listing
        # explicitly for lifecycle coverage in this test.
        with app.state.store._session_factory() as session:
            session.add(
                NormalizedJobRow(
                    id="seeded-job-1",
                    title="Backend Engineer",
                    company="Acme",
                    location="United States",
                    salary=None,
                    apply_url="https://boards.greenhouse.io/acme/jobs/123",
                    source="greenhouse",
                    posted_at=utc_now(),
                    description="Backend Python Engineer",
                    created_at=utc_now(),
                )
            )
            session.commit()

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

        # Worker-only execution model: queue first, worker claims and executes.
        assert app.state.store.claim_match_run(match_run_id) is True
        asyncio.run(app.state.matching.execute(match_run_id, assume_claimed=True))

        match_status_response = client.get(f"/v1/match-runs/{match_run_id}", headers=_auth_headers())
        assert match_status_response.status_code == 200
        match_status = match_status_response.json()
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

        assert app.state.store.claim_apply_run(apply_run_id) is True
        asyncio.run(app.state.apply.execute(apply_run_id, assume_claimed=True))

        apply_status_response = client.get(f"/v1/apply-runs/{apply_run_id}", headers=_auth_headers())
        assert apply_status_response.status_code == 200
        apply_status = apply_status_response.json()
        assert apply_status["status"] == "completed"
        assert len(apply_status["attempts"]) == 1


def test_run_claim_is_single_consumer() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as client:
        match_start = client.post(
            "/v1/match-runs",
            headers=_auth_headers(),
            json={
                "user_ref": "user-1",
                "resume_text": "Backend python engineer",
                "preferences": {"interests": ["backend"]},
                "limit": 3,
            },
        )
        assert match_start.status_code == 200
        match_run_id = match_start.json()["run_id"]
        assert app.state.store.claim_match_run(match_run_id) is True
        assert app.state.store.claim_match_run(match_run_id) is False

        apply_start = client.post(
            "/v1/apply-runs",
            headers=_auth_headers(),
            json={
                "user_ref": "user-1",
                "jobs": [
                    {
                        "external_job_id": "job-1",
                        "title": "Backend Engineer",
                        "company": "Acme",
                        "apply_url": "https://example.com/jobs/1",
                    }
                ],
                "profile_payload": {"full_name": "Jane Doe"},
                "daily_cap": 25,
            },
        )
        assert apply_start.status_code == 200
        apply_run_id = apply_start.json()["run_id"]
        assert app.state.store.claim_apply_run(apply_run_id) is True
        assert app.state.store.claim_apply_run(apply_run_id) is False


def test_search_hides_archived_jobs_by_default_and_includes_with_toggle() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app) as client:
        with app.state.store._session_factory() as session:
            session.add(
                NormalizedJobRow(
                    id="fresh-no-posted",
                    title="Fresh Engineer",
                    company="Acme",
                    location="United States",
                    salary=None,
                    apply_url="https://example.com/fresh",
                    source="greenhouse",
                    posted_at=None,
                    description="fresh engineer listing",
                    created_at=utc_now(),
                )
            )
            session.add(
                NormalizedJobRow(
                    id="old-with-posted",
                    title="Old Engineer With Posted",
                    company="Acme",
                    location="United States",
                    salary=None,
                    apply_url="https://example.com/old-posted",
                    source="lever",
                    posted_at=utc_now() - timedelta(days=30),
                    description="old posted engineer listing",
                    created_at=utc_now(),
                )
            )
            session.add(
                NormalizedJobRow(
                    id="old-no-posted",
                    title="Old Engineer No Posted",
                    company="Acme",
                    location="United States",
                    salary=None,
                    apply_url="https://example.com/old-created",
                    source="smartrecruiters",
                    posted_at=None,
                    description="old created engineer listing",
                    created_at=utc_now() - timedelta(days=45),
                )
            )
            session.commit()

        default_search = client.get(
            "/v1/jobs/search?q=engineer&limit=50",
            headers=_auth_headers(),
        )
        assert default_search.status_code == 200
        default_ids = {item["id"] for item in default_search.json()["jobs"]}
        assert "fresh-no-posted" in default_ids
        assert "old-with-posted" not in default_ids
        assert "old-no-posted" not in default_ids

        with_archive = client.get(
            "/v1/jobs/search?q=engineer&limit=50&include_archived=true",
            headers=_auth_headers(),
        )
        assert with_archive.status_code == 200
        archive_ids = {item["id"] for item in with_archive.json()["jobs"]}
        assert {"fresh-no-posted", "old-with-posted", "old-no-posted"}.issubset(archive_ids)


def test_apply_dev_review_mode_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_AUTONOMOUS_BROWSING", "true")
    monkeypatch.delenv("ENABLE_APPLY_DEV_REVIEW_MODE", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app):
        executor = app.state.apply._build_executor()
        assert isinstance(executor, PlaywrightApplyExecutor)
        assert executor.dev_review_mode is False


def test_apply_dev_review_mode_is_ignored_outside_dev_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_AUTONOMOUS_BROWSING", "true")
    monkeypatch.setenv("ENABLE_APPLY_DEV_REVIEW_MODE", "true")
    monkeypatch.setenv("APP_ENV", "production")

    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app):
        executor = app.state.apply._build_executor()
        assert isinstance(executor, PlaywrightApplyExecutor)
        assert executor.dev_review_mode is False


def test_apply_dev_review_mode_requires_autonomous_browsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_AUTONOMOUS_BROWSING", "false")
    monkeypatch.setenv("ENABLE_APPLY_DEV_REVIEW_MODE", "true")
    monkeypatch.setenv("APP_ENV", "development")

    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app):
        executor = app.state.apply._build_executor()
        assert isinstance(executor, SimulatedApplyExecutor)


def test_apply_dev_review_mode_enabled_when_both_flags_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENABLE_AUTONOMOUS_BROWSING", "true")
    monkeypatch.setenv("ENABLE_APPLY_DEV_REVIEW_MODE", "true")
    monkeypatch.setenv("APP_ENV", "development")

    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app):
        executor = app.state.apply._build_executor()
        assert isinstance(executor, PlaywrightApplyExecutor)
        assert executor.dev_review_mode is True


class _FakeHttpResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = f"status={status_code}"


class _FakeHttpClient:
    def __init__(self, status_codes: list[int]) -> None:
        self.status_codes = status_codes
        self.calls = 0

    def post(self, *_args, **_kwargs) -> _FakeHttpResponse:
        index = min(self.calls, len(self.status_codes) - 1)
        status_code = self.status_codes[index]
        self.calls += 1
        return _FakeHttpResponse(status_code=status_code)


def _build_callback_payload() -> ApplyAttemptCallbackPayload:
    return ApplyAttemptCallbackPayload(
        idempotency_key="idem-callback-retry",
        run_id="run-1",
        user_ref="user-1",
        attempt=ApplyAttemptRecord(
            attempt_id="attempt-1",
            external_job_id="job-1",
            job_url="https://example.com/jobs/1",
            status=ApplyAttemptStatus.succeeded,
        ),
    )


def test_callback_emitter_retries_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAIN_CALLBACK_URL", "https://callback.test/internal")
    monkeypatch.setattr(cloud_services_legacy.time, "sleep", lambda _seconds: None)

    client = _FakeHttpClient([500, 200])
    emitter = CallbackEmitter(http_client=client, max_attempts=3, retry_base_delay_ms=1)
    emitter.emit(_build_callback_payload())

    assert client.calls == 2


def test_callback_emitter_stops_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAIN_CALLBACK_URL", "https://callback.test/internal")
    monkeypatch.setattr(cloud_services_legacy.time, "sleep", lambda _seconds: None)

    client = _FakeHttpClient([500, 500, 500, 500])
    emitter = CallbackEmitter(http_client=client, max_attempts=3, retry_base_delay_ms=1)
    emitter.emit(_build_callback_payload())

    assert client.calls == 3
