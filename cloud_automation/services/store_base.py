from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from typing import Callable
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from common.time import utc_now

from ..db_models import (
    ApplyAttemptRow,
    ApplyRunRow,
    ArtifactRefRow,
    CrawlRunRow,
    JobFingerprintRow,
    JobSourceRow,
    MatchResultRow,
    MatchRunRow,
    NormalizedJobRow,
    RawJobDocumentRow,
)
from ..models import (
    ApplyAttemptRecord,
    ApplyAttemptStatus,
    ApplyRunRequest,
    ApplyRunStatusResponse,
    ArtifactRef,
    FailureCode,
    MatchRunRequest,
    MatchRunStatus,
    MatchRunStatusResponse,
    MatchedJob,
    NormalizedJob,
)

class JobIntelStoreBase:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        job_listing_ttl_days: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        if job_listing_ttl_days is None:
            raw_value = os.getenv("JOB_LISTING_TTL_DAYS", "21")
            try:
                parsed = int(raw_value)
            except ValueError:
                parsed = 21
            self._job_listing_ttl_days = max(parsed, 1)
        else:
            self._job_listing_ttl_days = max(int(job_listing_ttl_days), 1)

    def _archive_cutoff(self):
        return utc_now() - timedelta(days=self._job_listing_ttl_days)

    def record_discovery_documents(
        self,
        *,
        source_name: str,
        discovered_urls: list[str],
        raw_documents: dict[str, str],
        normalized_jobs: list[NormalizedJob],
        next_cursor: str | None,
    ) -> None:
        with self._session_factory() as session:
            source = session.get(JobSourceRow, source_name)
            if source is None:
                source = JobSourceRow(id=source_name, health_status="ok")
                session.add(source)

            source.last_cursor = next_cursor
            source.health_status = "ok"
            source.updated_at = utc_now()

            for url in discovered_urls:
                raw_doc_id = str(uuid4())
                session.add(
                    RawJobDocumentRow(
                        id=raw_doc_id,
                        source_id=source_name,
                        url=url,
                        body=raw_documents[url],
                        fetched_at=utc_now(),
                    )
                )

            for job in normalized_jobs:
                fingerprint = self._job_fingerprint(job)
                existing_fp = session.scalar(
                    select(JobFingerprintRow).where(
                        JobFingerprintRow.fingerprint == fingerprint
                    )
                )
                if existing_fp is not None:
                    existing_job = session.get(NormalizedJobRow, existing_fp.canonical_job_id)
                    if existing_job is not None:
                        existing_job.title = job.title
                        existing_job.company = job.company
                        existing_job.location = job.location
                        existing_job.salary = job.salary
                        existing_job.apply_url = job.apply_url
                        existing_job.source = job.source
                        existing_job.posted_at = job.posted_at
                        existing_job.description = job.description
                        continue

                session.add(
                    NormalizedJobRow(
                        id=job.id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        salary=job.salary,
                        apply_url=job.apply_url,
                        source=job.source,
                        posted_at=job.posted_at,
                        description=job.description,
                        created_at=utc_now(),
                    )
                )
                session.add(
                    JobFingerprintRow(
                        id=str(uuid4()),
                        fingerprint=fingerprint,
                        canonical_job_id=job.id,
                        created_at=utc_now(),
                    )
                )

            session.commit()

    def create_crawl_run(self, source_count: int) -> str:
        run_id = str(uuid4())
        with self._session_factory() as session:
            session.add(
                CrawlRunRow(
                    id=run_id,
                    status="running",
                    source_count=source_count,
                    discovered_count=0,
                    started_at=utc_now(),
                )
            )
            session.commit()
        return run_id

    def finalize_crawl_run(
        self, *, run_id: str, discovered_count: int, error: str | None = None
    ) -> None:
        with self._session_factory() as session:
            row = session.get(CrawlRunRow, run_id)
            if row is None:
                return
            row.discovered_count = discovered_count
            row.status = "failed" if error else "completed"
            row.error = error
            row.completed_at = utc_now()
            session.commit()

    def search_jobs(
        self,
        *,
        keywords: list[str],
        location: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[NormalizedJob]:
        normalized_limit = min(max(limit, 1), 100)
        normalized_keywords = [item.strip().lower() for item in keywords if item.strip()]
        normalized_location = location.strip().lower() if isinstance(location, str) and location.strip() else None

        filters = []
        if not include_archived:
            filters.append(
                func.coalesce(NormalizedJobRow.posted_at, NormalizedJobRow.created_at)
                >= self._archive_cutoff()
            )
        if normalized_location:
            location_pattern = f"%{normalized_location}%"
            filters.append(
                func.lower(func.coalesce(NormalizedJobRow.location, "")).like(location_pattern)
            )
        if normalized_keywords:
            keyword_clauses = []
            for keyword in normalized_keywords:
                pattern = f"%{keyword}%"
                keyword_clauses.append(
                    or_(
                        func.lower(func.coalesce(NormalizedJobRow.title, "")).like(pattern),
                        func.lower(func.coalesce(NormalizedJobRow.description, "")).like(pattern),
                        func.lower(func.coalesce(NormalizedJobRow.company, "")).like(pattern),
                    )
                )
            filters.append(or_(*keyword_clauses))

        stmt = select(NormalizedJobRow).order_by(NormalizedJobRow.created_at.desc()).limit(normalized_limit)
        if filters:
            stmt = stmt.where(and_(*filters))

        with self._session_factory() as session:
            rows = session.scalars(stmt).all()
            return [self._to_normalized_job(row) for row in rows]

    def create_match_run(self, request: MatchRunRequest) -> str:
        run_id = str(uuid4())
        with self._session_factory() as session:
            session.add(
                MatchRunRow(
                    id=run_id,
                    user_ref=request.user_ref,
                    status=MatchRunStatus.queued.value,
                    request_json=request.model_dump_json(),
                    started_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            session.commit()
        return run_id

    def list_queued_match_run_ids(self, *, limit: int = 50) -> list[str]:
        with self._session_factory() as session:
            return session.scalars(
                select(MatchRunRow.id)
                .where(MatchRunRow.status == MatchRunStatus.queued.value)
                .order_by(MatchRunRow.started_at.asc())
                .limit(max(limit, 1))
            ).all()

    def claim_match_run(self, run_id: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                update(MatchRunRow)
                .where(
                    and_(
                        MatchRunRow.id == run_id,
                        MatchRunRow.status == MatchRunStatus.queued.value,
                    )
                )
                .values(status=MatchRunStatus.running.value, updated_at=utc_now())
            )
            session.commit()
            return bool(result.rowcount)

    def set_match_run_status(
        self, *, run_id: str, status: MatchRunStatus, error: str | None = None
    ) -> None:
        with self._session_factory() as session:
            row = session.get(MatchRunRow, run_id)
            if row is None:
                return
            row.status = status.value
            row.error = error
            row.updated_at = utc_now()
            session.commit()

    def get_match_run_request(self, run_id: str) -> MatchRunRequest:
        with self._session_factory() as session:
            row = session.get(MatchRunRow, run_id)
            if row is None:
                raise ValueError("Match run not found")
            return MatchRunRequest.model_validate_json(row.request_json)

    def replace_match_results(self, *, run_id: str, matches: list[MatchedJob]) -> None:
        with self._session_factory() as session:
            existing = session.scalars(
                select(MatchResultRow).where(MatchResultRow.run_id == run_id)
            ).all()
            for row in existing:
                session.delete(row)

            for match in matches:
                session.add(
                    MatchResultRow(
                        id=str(uuid4()),
                        run_id=run_id,
                        external_job_id=match.external_job_id,
                        title=match.title,
                        company=match.company,
                        location=match.location,
                        apply_url=match.apply_url,
                        source=match.source,
                        reason=match.reason,
                        score=match.score,
                        posted_at=match.posted_at,
                    )
                )

            session.commit()

    def get_match_run_status(self, run_id: str) -> MatchRunStatusResponse:
        with self._session_factory() as session:
            run_row = session.get(MatchRunRow, run_id)
            if run_row is None:
                raise ValueError("Match run not found")
            result_rows = session.scalars(
                select(MatchResultRow).where(MatchResultRow.run_id == run_id)
            ).all()

        return MatchRunStatusResponse(
            run_id=run_id,
            status=MatchRunStatus(run_row.status),
            matches=[
                MatchedJob(
                    external_job_id=item.external_job_id,
                    title=item.title,
                    company=item.company,
                    location=item.location,
                    apply_url=item.apply_url,
                    source=item.source,
                    reason=item.reason,
                    score=item.score,
                    posted_at=item.posted_at,
                )
                for item in result_rows
            ],
            error=run_row.error,
        )

    def create_apply_run(self, request: ApplyRunRequest) -> str:
        run_id = str(uuid4())
        with self._session_factory() as session:
            now = utc_now()
            session.add(
                ApplyRunRow(
                    id=run_id,
                    user_ref=request.user_ref,
                    status=MatchRunStatus.queued.value,
                    request_json=request.model_dump_json(),
                    started_at=now,
                    updated_at=now,
                )
            )
            # PostgreSQL enforces foreign keys immediately; persist the parent run row
            # before enqueueing child apply attempts.
            session.flush()

            for job in request.jobs:
                session.add(
                    ApplyAttemptRow(
                        id=str(uuid4()),
                        run_id=run_id,
                        external_job_id=job.external_job_id,
                        job_url=job.apply_url,
                        status=ApplyAttemptStatus.queued.value,
                        created_at=now,
                        updated_at=now,
                    )
                )

            session.commit()
        return run_id

    def list_queued_apply_run_ids(self, *, limit: int = 50) -> list[str]:
        with self._session_factory() as session:
            return session.scalars(
                select(ApplyRunRow.id)
                .where(ApplyRunRow.status == MatchRunStatus.queued.value)
                .order_by(ApplyRunRow.started_at.asc())
                .limit(max(limit, 1))
            ).all()

    def claim_apply_run(self, run_id: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                update(ApplyRunRow)
                .where(
                    and_(
                        ApplyRunRow.id == run_id,
                        ApplyRunRow.status == MatchRunStatus.queued.value,
                    )
                )
                .values(status=MatchRunStatus.running.value, updated_at=utc_now())
            )
            session.commit()
            return bool(result.rowcount)

    def set_apply_run_status(
        self, *, run_id: str, status: MatchRunStatus, error: str | None = None
    ) -> None:
        with self._session_factory() as session:
            row = session.get(ApplyRunRow, run_id)
            if row is None:
                return
            row.status = status.value
            row.error = error
            row.updated_at = utc_now()
            session.commit()

    def get_apply_run_request(self, run_id: str) -> ApplyRunRequest:
        with self._session_factory() as session:
            row = session.get(ApplyRunRow, run_id)
            if row is None:
                raise ValueError("Apply run not found")
            return ApplyRunRequest.model_validate_json(row.request_json)

    def list_apply_attempts(self, run_id: str) -> list[ApplyAttemptRecord]:
        with self._session_factory() as session:
            attempts = session.scalars(
                select(ApplyAttemptRow).where(ApplyAttemptRow.run_id == run_id)
            ).all()
            artifacts = session.scalars(
                select(ArtifactRefRow).where(
                    ArtifactRefRow.attempt_id.in_([item.id for item in attempts])
                )
            ).all()

        artifact_map: dict[str, list[ArtifactRef]] = {}
        for artifact in artifacts:
            artifact_map.setdefault(artifact.attempt_id, []).append(
                ArtifactRef(
                    kind=artifact.kind,
                    url=artifact.url,
                    expires_at=artifact.expires_at,
                )
            )

        return [
            ApplyAttemptRecord(
                attempt_id=attempt.id,
                external_job_id=attempt.external_job_id,
                job_url=attempt.job_url,
                status=ApplyAttemptStatus(attempt.status),
                failure_code=FailureCode(attempt.failure_code)
                if attempt.failure_code
                else None,
                failure_reason=attempt.failure_reason,
                submitted_at=attempt.submitted_at,
                artifacts=artifact_map.get(attempt.id, []),
            )
            for attempt in attempts
        ]

    def update_apply_attempt(self, run_id: str, attempt: ApplyAttemptRecord) -> None:
        with self._session_factory() as session:
            row = session.get(ApplyAttemptRow, attempt.attempt_id)
            if row is None:
                row = ApplyAttemptRow(
                    id=attempt.attempt_id,
                    run_id=run_id,
                    external_job_id=attempt.external_job_id,
                    job_url=attempt.job_url,
                    status=attempt.status.value,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(row)

            row.external_job_id = attempt.external_job_id
            row.job_url = attempt.job_url
            row.status = attempt.status.value
            row.failure_code = attempt.failure_code.value if attempt.failure_code else None
            row.failure_reason = attempt.failure_reason
            row.submitted_at = attempt.submitted_at
            row.updated_at = utc_now()

            existing_artifacts = session.scalars(
                select(ArtifactRefRow).where(ArtifactRefRow.attempt_id == attempt.attempt_id)
            ).all()
            for artifact in existing_artifacts:
                session.delete(artifact)

            for artifact in attempt.artifacts:
                session.add(
                    ArtifactRefRow(
                        id=str(uuid4()),
                        attempt_id=attempt.attempt_id,
                        kind=artifact.kind,
                        url=artifact.url,
                        expires_at=artifact.expires_at,
                        created_at=utc_now(),
                    )
                )
            session.commit()

    def get_apply_run_status(self, run_id: str) -> ApplyRunStatusResponse:
        with self._session_factory() as session:
            run_row = session.get(ApplyRunRow, run_id)
            if run_row is None:
                raise ValueError("Apply run not found")

        attempts = self.list_apply_attempts(run_id)
        return ApplyRunStatusResponse(
            run_id=run_id,
            status=MatchRunStatus(run_row.status),
            attempts=attempts,
            error=run_row.error,
        )

    @staticmethod
    def _job_fingerprint(job: NormalizedJob) -> str:
        source = f"{job.title.strip().lower()}::{job.company.strip().lower()}::{(job.location or '').strip().lower()}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_normalized_job(row: NormalizedJobRow) -> NormalizedJob:
        return NormalizedJob(
            id=row.id,
            title=row.title,
            company=row.company,
            location=row.location,
            salary=row.salary,
            apply_url=row.apply_url,
            source=row.source,
            posted_at=row.posted_at,
            description=row.description,
        )



__all__ = ["JobIntelStoreBase"]
