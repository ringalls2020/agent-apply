from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from common.time import utc_now
from cloud_automation.db import Base, create_db_engine, create_session_factory
from cloud_automation.db_models import (
    AtsTokenEvidenceRow,
    AtsTokenRow,
    DiscoverySeedRow,
    JobIdentityRow,
    NormalizedJobRow,
)
from cloud_automation.services import CommonCrawlCoordinator, DiscoveryCoordinator, JobIntelStore
from cloud_automation.services.ats_token_utils import build_job_identity, extract_ats_tokens_from_text
from cloud_automation.services.discovery_pipeline import DiscoveryPipeline
from cloud_automation.services.token_registry import TokenRegistryCoordinator
from cloud_automation.workers import job_dedupe_backfill


@pytest.fixture
def store() -> JobIntelStore:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    yield JobIntelStore(create_session_factory(engine))
    engine.dispose()


def test_extract_ats_tokens_patterns() -> None:
    html = """
    <script src="https://boards.greenhouse.io/embed/job_board/js?for=AcmeCorp"></script>
    <a href="https://boards.greenhouse.io/AcmeCorp">GH Hosted</a>
    <a href="https://jobs.lever.co/LeverCo">Lever Hosted</a>
    <script>fetch("https://api.smartrecruiters.com/v1/companies/SmartCo/postings")</script>
    <a href="https://boards.greenhouse.io/embed">reserved</a>
    """
    extracted = extract_ats_tokens_from_text(html)
    normalized = {(item.provider, item.token) for item in extracted}
    assert ("greenhouse", "acmecorp") in normalized
    assert ("lever", "leverco") in normalized
    assert ("smartrecruiters", "smartco") in normalized
    assert ("greenhouse", "embed") not in normalized


def test_robots_disallow_skips_seed(store: JobIntelStore) -> None:
    store.upsert_discovery_seeds(
        manifest_url="https://seed.test/seeds.json",
        seeds=[("Acme", "https://acme.example/careers")],
    )
    seed = store.list_discovery_seeds(limit=1)[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://acme.example/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pipeline = DiscoveryPipeline(store=store, http_client=client)
    inserted = pipeline._crawl_seed(seed)
    client.close()

    assert inserted == 0
    refreshed = store.list_discovery_seeds(limit=1)[0]
    assert refreshed.status == "robots_blocked"


def test_robots_fetch_failure_skips_seed(store: JobIntelStore) -> None:
    store.upsert_discovery_seeds(
        manifest_url="https://seed.test/seeds.json",
        seeds=[("Acme", "https://acme.example/careers")],
    )
    seed = store.list_discovery_seeds(limit=1)[0]

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://acme.example/robots.txt":
            return httpx.Response(503, text="down")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pipeline = DiscoveryPipeline(store=store, http_client=client)
    inserted = pipeline._crawl_seed(seed)
    client.close()

    assert inserted == 0
    refreshed = store.list_discovery_seeds(limit=1)[0]
    assert refreshed.status == "robots_error"


def test_crawl_delay_is_respected(store: JobIntelStore, monkeypatch: pytest.MonkeyPatch) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, text="ok")))
    pipeline = DiscoveryPipeline(store=store, http_client=client)
    pipeline._last_domain_request_at["acme.example"] = time.monotonic()
    captured: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda value: captured.append(value))
    pipeline._respect_domain_delay(domain="acme.example", crawl_delay_seconds=1)
    client.close()
    assert captured and captured[0] > 0


def test_token_upsert_deduplicates_and_preserves_evidence(store: JobIntelStore) -> None:
    inserted_a = store.record_extracted_tokens(
        extracted_tokens=extract_ats_tokens_from_text(
            "https://boards.greenhouse.io/embed/job_board/js?for=acme"
        ),
        method="method_a",
        evidence_url="https://acme.example/careers",
    )
    inserted_b = store.record_extracted_tokens(
        extracted_tokens=extract_ats_tokens_from_text(
            "https://boards.greenhouse.io/embed/job_board/js?for=acme"
        ),
        method="method_b",
        evidence_url="https://index.commoncrawl.org/record-1",
    )
    assert inserted_a == 1
    assert inserted_b == 0

    with store._session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AtsTokenRow)) == 1
        assert session.scalar(select(func.count()).select_from(AtsTokenEvidenceRow)) == 2


def test_token_validation_lifecycle(store: JobIntelStore) -> None:
    store.record_extracted_tokens(
        extracted_tokens=extract_ats_tokens_from_text(
            "https://boards.greenhouse.io/embed/job_board/js?for=okco"
        ),
        method="method_a",
        evidence_url="https://okco.example/careers",
    )
    store.record_extracted_tokens(
        extracted_tokens=extract_ats_tokens_from_text(
            "https://boards.greenhouse.io/embed/job_board/js?for=missingco"
        ),
        method="method_a",
        evidence_url="https://missingco.example/careers",
    )
    store.record_extracted_tokens(
        extracted_tokens=extract_ats_tokens_from_text(
            "https://boards.greenhouse.io/embed/job_board/js?for=slowco"
        ),
        method="method_a",
        evidence_url="https://slowco.example/careers",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "boards-api.greenhouse.io/v1/boards/okco/jobs" in url:
            return httpx.Response(200, json={"jobs": []})
        if "boards-api.greenhouse.io/v1/boards/missingco/jobs" in url:
            return httpx.Response(404, json={"error": "missing"})
        if "boards-api.greenhouse.io/v1/boards/slowco/jobs" in url:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    coordinator = TokenRegistryCoordinator(store=store, http_client=client)
    stats = coordinator.validate_tokens_once()
    client.close()

    with store._session_factory() as session:
        statuses = {
            row.token: row.status
            for row in session.scalars(select(AtsTokenRow)).all()
        }
    assert statuses["okco"] == "validated"
    assert statuses["missingco"] == "invalid"
    assert statuses["slowco"] == "pending"
    assert stats["validated"] == 1
    assert stats["invalid"] == 1
    assert stats["pending"] == 1


def test_build_job_identity_canonical_keys() -> None:
    greenhouse = build_job_identity(
        source="greenhouse",
        apply_url="https://boards.greenhouse.io/acme/jobs/123",
        external_job_id="greenhouse-acme-123",
    )
    lever = build_job_identity(
        source="lever",
        apply_url="https://jobs.lever.co/acme/abc-123",
        external_job_id="lever-acme-abc-123",
    )
    smart = build_job_identity(
        source="smartrecruiters",
        apply_url="https://jobs.smartrecruiters.com/acme/xyz-999",
        external_job_id="smartrecruiters-acme-xyz-999",
    )
    fallback = build_job_identity(
        source="other",
        apply_url="https://careers.example/jobs/1?utm=foo",
        external_job_id="job-1",
    )
    assert greenhouse.canonical_key == "greenhouse:acme:123"
    assert lever.canonical_key == "lever:acme:abc-123"
    assert smart.canonical_key == "smartrecruiters:acme:xyz-999"
    assert fallback.canonical_key.startswith("other:")


def test_method_a_pipeline_ingests_validated_feed_jobs(
    store: JobIntelStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEED_MANIFEST_URLS", "https://seed.test/method-a.json")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://seed.test/method-a.json":
            return httpx.Response(
                200,
                text=json.dumps(
                    [{"company": "Acme", "careers_url": "https://acme.example/careers"}]
                ),
            )
        if url == "https://acme.example/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 0\n")
        if url == "https://acme.example/careers":
            return httpx.Response(
                200,
                text='<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>',
                headers={"etag": "seed-etag"},
            )
        if "boards-api.greenhouse.io/v1/boards/acme/jobs" in url:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Backend Engineer",
                            "location": {"name": "United States"},
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                            "updated_at": utc_now().isoformat(),
                            "content": "Python backend role",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    coordinator = DiscoveryCoordinator(store=store, http_client=client)
    coordinator.run_discovery_once()
    client.close()

    jobs = store.search_jobs(keywords=["backend"], location="United States", limit=10)
    assert len(jobs) == 1
    assert jobs[0].source == "greenhouse"
    assert jobs[0].company == "acme"


def test_method_b_common_crawl_extracts_then_ingests(
    store: JobIntelStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEED_MANIFEST_URLS", raising=False)
    monkeypatch.setenv("COMMON_CRAWL_LOOKBACK_INDEXES", "1")
    monkeypatch.setenv("COMMON_CRAWL_MAX_PAGES_PER_PATTERN", "1")
    monkeypatch.setenv("COMMON_CRAWL_MAX_RECORDS_PER_PATTERN", "10")

    cc_line = json.dumps(
        {"url": "https://boards.greenhouse.io/embed/job_board/js?for=beta"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://index.commoncrawl.org/collinfo.json":
            return httpx.Response(200, json=[{"id": "CC-MAIN-2026-06"}])
        if "CC-MAIN-2026-06-index" in url and "boards.greenhouse.io" in url:
            return httpx.Response(200, text=f"{cc_line}\n")
        if "CC-MAIN-2026-06-index" in url:
            return httpx.Response(200, text="")
        if "boards-api.greenhouse.io/v1/boards/beta/jobs" in url:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 42,
                            "title": "Platform Engineer",
                            "location": {"name": "United States"},
                            "absolute_url": "https://boards.greenhouse.io/beta/jobs/42",
                            "updated_at": utc_now().isoformat(),
                            "content": "Platform engineering role",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    common_crawl = CommonCrawlCoordinator(store=store, http_client=client)
    discovery = DiscoveryCoordinator(store=store, http_client=client)
    common_crawl.run_common_crawl_once()
    discovery.run_discovery_once()
    client.close()

    jobs = store.search_jobs(keywords=["platform"], location="United States", limit=10)
    assert len(jobs) == 1
    assert jobs[0].id == "greenhouse-beta-42"


def test_duplicate_prevention_when_method_a_and_b_find_same_job(
    store: JobIntelStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEED_MANIFEST_URLS", "https://seed.test/method-a.json")
    monkeypatch.setenv("COMMON_CRAWL_LOOKBACK_INDEXES", "1")
    monkeypatch.setenv("COMMON_CRAWL_MAX_PAGES_PER_PATTERN", "1")
    monkeypatch.setenv("COMMON_CRAWL_MAX_RECORDS_PER_PATTERN", "10")

    cc_line = json.dumps(
        {"url": "https://boards.greenhouse.io/embed/job_board/js?for=acme"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://seed.test/method-a.json":
            return httpx.Response(
                200,
                text=json.dumps(
                    [{"company": "Acme", "careers_url": "https://acme.example/careers"}]
                ),
            )
        if url == "https://acme.example/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if url == "https://acme.example/careers":
            return httpx.Response(
                200,
                text='<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>',
            )
        if url == "https://index.commoncrawl.org/collinfo.json":
            return httpx.Response(200, json=[{"id": "CC-MAIN-2026-06"}])
        if "CC-MAIN-2026-06-index" in url and "boards.greenhouse.io" in url:
            return httpx.Response(200, text=f"{cc_line}\n")
        if "CC-MAIN-2026-06-index" in url:
            return httpx.Response(200, text="")
        if "boards-api.greenhouse.io/v1/boards/acme/jobs" in url:
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 7,
                            "title": "Automation Engineer",
                            "location": {"name": "United States"},
                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/7",
                            "updated_at": utc_now().isoformat(),
                            "content": "Automation role",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    discovery = DiscoveryCoordinator(store=store, http_client=client)
    common_crawl = CommonCrawlCoordinator(store=store, http_client=client)

    discovery.run_discovery_once()
    common_crawl.run_common_crawl_once()
    discovery.run_discovery_once()
    client.close()

    with store._session_factory() as session:
        job_count = session.scalar(select(func.count()).select_from(NormalizedJobRow))
        identity_count = session.scalar(select(func.count()).select_from(JobIdentityRow))
    assert job_count == 1
    assert identity_count == 1


def test_job_dedupe_backfill_merges_existing_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "jobs_backfill.db"
    monkeypatch.setenv("JOBS_DATABASE_URL", f"sqlite+pysqlite:///{db_path}")

    engine = create_db_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        session.add(
            NormalizedJobRow(
                id="dup-1",
                title="Backend Engineer",
                company="Acme",
                location="United States",
                salary=None,
                apply_url="https://boards.greenhouse.io/acme/jobs/1",
                source="greenhouse",
                posted_at=utc_now(),
                description="first",
                created_at=utc_now(),
            )
        )
        session.add(
            NormalizedJobRow(
                id="dup-2",
                title="Backend Engineer",
                company="Acme",
                location="United States",
                salary=None,
                apply_url="https://boards.greenhouse.io/acme/jobs/1",
                source="greenhouse",
                posted_at=utc_now(),
                description="second",
                created_at=utc_now(),
            )
        )
        session.commit()
    engine.dispose()

    job_dedupe_backfill.run()

    verify_engine = create_db_engine(f"sqlite+pysqlite:///{db_path}")
    verify_session_factory = create_session_factory(verify_engine)
    with verify_session_factory() as session:
        assert session.scalar(select(func.count()).select_from(NormalizedJobRow)) == 1
        assert session.scalar(select(func.count()).select_from(JobIdentityRow)) == 1
    verify_engine.dispose()
