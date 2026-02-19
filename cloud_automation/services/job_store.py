from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Iterable
from urllib.parse import urlsplit

from sqlalchemy import and_, or_, select, tuple_, update

from common.time import utc_now

from ..db_models import (
    AtsTokenEvidenceRow,
    AtsTokenRow,
    DiscoveryRefreshRequestRow,
    DiscoverySeedRow,
    DomainRobotsCacheRow,
    JobFingerprintRow,
    JobIdentityRow,
    JobSourceRow,
    NormalizedJobRow,
    RawJobDocumentRow,
    SeedManifestBuildRunRow,
    SeedManifestEntryRow,
)
from ..models import NormalizedJob
from .ats_token_utils import ExtractedToken, JobIdentity, build_job_identity
from .store_base import JobIntelStoreBase


class JobIntelStore(JobIntelStoreBase):
    def record_discovery_documents(
        self,
        *,
        source_name: str,
        discovered_urls: list[str],
        raw_documents: dict[str, str],
        normalized_jobs: list[NormalizedJob],
        next_cursor: str | None,
    ) -> None:
        now = utc_now()
        with self._session_factory() as session:
            source = session.get(JobSourceRow, source_name)
            if source is None:
                source = JobSourceRow(id=source_name, health_status="ok")
                session.add(source)

            source.last_cursor = next_cursor
            source.health_status = "ok"
            source.updated_at = now

            for url in discovered_urls:
                if url not in raw_documents:
                    continue
                session.add(
                    RawJobDocumentRow(
                        id=str(uuid.uuid4()),
                        source_id=source_name,
                        url=url,
                        body=raw_documents[url],
                        fetched_at=now,
                    )
                )

            if not normalized_jobs:
                session.commit()
                return

            deduped_jobs: dict[str, tuple[NormalizedJob, JobIdentity, str]] = {}
            for job in normalized_jobs:
                identity = build_job_identity(
                    source=job.source,
                    apply_url=job.apply_url,
                    external_job_id=job.id,
                )
                deduped_jobs[identity.canonical_key] = (
                    job,
                    identity,
                    self._job_fingerprint(job),
                )

            prepared_jobs = list(deduped_jobs.values())
            canonical_keys = [identity.canonical_key for _, identity, _ in prepared_jobs]
            fingerprints = [fingerprint for _, _, fingerprint in prepared_jobs]
            input_job_ids = [job.id for job, _, _ in prepared_jobs]

            existing_identity_rows = session.scalars(
                select(JobIdentityRow).where(JobIdentityRow.canonical_key.in_(canonical_keys))
            ).all()
            identity_by_key = {row.canonical_key: row for row in existing_identity_rows}

            referenced_job_ids = {
                row.canonical_job_id for row in existing_identity_rows if row.canonical_job_id
            }
            job_ids_to_load = set(input_job_ids) | referenced_job_ids
            existing_job_rows = (
                session.scalars(select(NormalizedJobRow).where(NormalizedJobRow.id.in_(job_ids_to_load))).all()
                if job_ids_to_load
                else []
            )
            job_by_id = {row.id: row for row in existing_job_rows}

            existing_fingerprint_rows = (
                session.scalars(
                    select(JobFingerprintRow).where(JobFingerprintRow.fingerprint.in_(fingerprints))
                ).all()
                if fingerprints
                else []
            )
            fingerprint_by_value = {row.fingerprint: row for row in existing_fingerprint_rows}

            resolved_records: list[tuple[NormalizedJob, JobIdentity, str, str]] = []
            for job, identity, fingerprint in prepared_jobs:
                identity_row = identity_by_key.get(identity.canonical_key)
                target_job = None
                if identity_row is not None:
                    target_job = job_by_id.get(identity_row.canonical_job_id)
                if target_job is None:
                    target_job = job_by_id.get(job.id)

                if target_job is None:
                    target_job = NormalizedJobRow(
                        id=job.id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        salary=job.salary,
                        apply_url=identity.normalized_apply_url,
                        source=job.source,
                        posted_at=job.posted_at,
                        description=job.description,
                        created_at=now,
                    )
                    session.add(target_job)
                    job_by_id[target_job.id] = target_job
                else:
                    target_job.title = job.title
                    target_job.company = job.company
                    target_job.location = job.location
                    target_job.salary = job.salary
                    target_job.apply_url = identity.normalized_apply_url
                    target_job.source = job.source
                    target_job.posted_at = job.posted_at
                    target_job.description = job.description

                resolved_records.append((job, identity, fingerprint, target_job.id))

            # Ensure parent rows exist before writing identity/fingerprint rows.
            session.flush()

            for _job, identity, fingerprint, canonical_job_id in resolved_records:
                identity_row = identity_by_key.get(identity.canonical_key)
                if identity_row is None:
                    identity_row = JobIdentityRow(
                        id=str(uuid.uuid4()),
                        canonical_key=identity.canonical_key,
                        canonical_job_id=canonical_job_id,
                        provider=identity.provider,
                        provider_token=identity.provider_token,
                        provider_job_id=identity.provider_job_id,
                        normalized_apply_url_hash=identity.normalized_apply_url_hash,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(identity_row)
                    identity_by_key[identity.canonical_key] = identity_row
                else:
                    identity_row.canonical_job_id = canonical_job_id
                    identity_row.provider = identity.provider
                    identity_row.provider_token = identity.provider_token
                    identity_row.provider_job_id = identity.provider_job_id
                    identity_row.normalized_apply_url_hash = identity.normalized_apply_url_hash
                    identity_row.last_seen_at = now

                fingerprint_row = fingerprint_by_value.get(fingerprint)
                if fingerprint_row is None:
                    fingerprint_row = JobFingerprintRow(
                        id=str(uuid.uuid4()),
                        fingerprint=fingerprint,
                        canonical_job_id=canonical_job_id,
                        created_at=now,
                    )
                    session.add(fingerprint_row)
                    fingerprint_by_value[fingerprint] = fingerprint_row
                else:
                    fingerprint_row.canonical_job_id = canonical_job_id

            session.commit()

    def upsert_discovery_seeds(
        self,
        *,
        manifest_url: str,
        seeds: Iterable[tuple[str | None, str]],
    ) -> int:
        now = utc_now()
        normalized_seeds: dict[str, tuple[str | None, str]] = {}
        for company, careers_url in seeds:
            normalized_url = careers_url.strip()
            if not normalized_url:
                continue
            domain = (urlsplit(normalized_url).hostname or "").lower()
            if not domain:
                continue
            normalized_company = company.strip() if isinstance(company, str) and company.strip() else None
            normalized_seeds[normalized_url] = (normalized_company, domain)

        if not normalized_seeds:
            return 0

        with self._session_factory() as session:
            existing_rows = session.scalars(
                select(DiscoverySeedRow).where(
                    DiscoverySeedRow.careers_url.in_(normalized_seeds.keys())
                )
            ).all()
            existing_by_url = {row.careers_url: row for row in existing_rows}

            for careers_url, (company, domain) in normalized_seeds.items():
                row = existing_by_url.get(careers_url)
                if row is None:
                    session.add(
                        DiscoverySeedRow(
                            id=str(uuid.uuid4()),
                            company=company,
                            careers_url=careers_url,
                            domain=domain,
                            source_manifest_url=manifest_url,
                            status="pending",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    continue

                row.company = company or row.company
                row.source_manifest_url = manifest_url
                row.updated_at = now
            session.commit()
        return len(normalized_seeds)

    def list_discovery_seeds(self, *, limit: int = 2000) -> list[DiscoverySeedRow]:
        with self._session_factory() as session:
            return session.scalars(
                select(DiscoverySeedRow)
                .where(DiscoverySeedRow.status != "disabled")
                .order_by(DiscoverySeedRow.updated_at.asc())
                .limit(max(limit, 1))
            ).all()

    def mark_discovery_seed_result(
        self,
        *,
        careers_url: str,
        status: str,
        etag: str | None = None,
        last_modified: str | None = None,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self._session_factory() as session:
            row = session.scalar(
                select(DiscoverySeedRow).where(DiscoverySeedRow.careers_url == careers_url)
            )
            if row is None:
                return
            row.status = status
            row.etag = etag
            row.last_modified = last_modified
            row.last_error = error
            row.last_crawled_at = now
            row.updated_at = now
            session.commit()

    def get_domain_robots_cache(self, *, domain: str) -> DomainRobotsCacheRow | None:
        with self._session_factory() as session:
            return session.get(DomainRobotsCacheRow, domain.lower())

    def upsert_domain_robots_cache(
        self,
        *,
        domain: str,
        robots_url: str,
        robots_txt: str,
        crawl_delay_seconds: int | None,
        status: str,
        error: str | None,
        ttl_seconds: int,
    ) -> None:
        now = utc_now()
        expires_at = now + timedelta(seconds=max(ttl_seconds, 60))
        with self._session_factory() as session:
            row = session.get(DomainRobotsCacheRow, domain.lower())
            if row is None:
                row = DomainRobotsCacheRow(
                    domain=domain.lower(),
                    robots_url=robots_url,
                    robots_txt=robots_txt,
                    crawl_delay_seconds=crawl_delay_seconds,
                    status=status,
                    last_error=error,
                    fetched_at=now,
                    expires_at=expires_at,
                )
                session.add(row)
            else:
                row.robots_url = robots_url
                row.robots_txt = robots_txt
                row.crawl_delay_seconds = crawl_delay_seconds
                row.status = status
                row.last_error = error
                row.fetched_at = now
                row.expires_at = expires_at
            session.commit()

    def record_extracted_tokens(
        self,
        *,
        extracted_tokens: Iterable[ExtractedToken],
        method: str,
        evidence_url: str,
    ) -> int:
        now = utc_now()
        token_pairs = sorted(
            {
                (token.provider, token.token)
                for token in extracted_tokens
                if token.provider and token.token
            }
        )
        if not token_pairs:
            return 0

        inserted = 0
        with self._session_factory() as session:
            existing_rows = session.scalars(
                select(AtsTokenRow).where(
                    tuple_(AtsTokenRow.provider, AtsTokenRow.token).in_(token_pairs)
                )
            ).all()
            token_by_pair = {(row.provider, row.token): row for row in existing_rows}

            for provider, token in token_pairs:
                row = token_by_pair.get((provider, token))
                if row is None:
                    row = AtsTokenRow(
                        id=str(uuid.uuid4()),
                        provider=provider,
                        token=token,
                        status="pending",
                        discovered_method=method,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(row)
                    token_by_pair[(provider, token)] = row
                    inserted += 1
                    continue

                row.last_seen_at = now
                discovered_methods = {
                    item.strip()
                    for item in row.discovered_method.split(",")
                    if item.strip()
                }
                if method not in discovered_methods:
                    discovered_methods.add(method)
                    row.discovered_method = ",".join(sorted(discovered_methods))

            # Session autoflush is disabled; ensure token parent rows are persisted
            # before inserting evidence rows that reference token_id.
            session.flush()

            token_rows = [token_by_pair[pair] for pair in token_pairs]
            token_ids = [row.id for row in token_rows]
            existing_evidence_token_ids = set(
                session.scalars(
                    select(AtsTokenEvidenceRow.token_id).where(
                        and_(
                            AtsTokenEvidenceRow.token_id.in_(token_ids),
                            AtsTokenEvidenceRow.method == method,
                            AtsTokenEvidenceRow.evidence_url == evidence_url,
                        )
                    )
                ).all()
            )

            for row in token_rows:
                if row.id in existing_evidence_token_ids:
                    continue
                session.add(
                    AtsTokenEvidenceRow(
                        id=str(uuid.uuid4()),
                        token_id=row.id,
                        method=method,
                        evidence_url=evidence_url,
                        discovered_at=now,
                    )
                )
            session.commit()
        return inserted

    def list_tokens_for_validation(
        self,
        *,
        recheck_hours: int,
        limit: int = 1000,
    ) -> list[AtsTokenRow]:
        cutoff = utc_now() - timedelta(hours=max(recheck_hours, 1))
        with self._session_factory() as session:
            return session.scalars(
                select(AtsTokenRow)
                .where(
                    or_(
                        AtsTokenRow.status == "pending",
                        AtsTokenRow.last_validated_at.is_(None),
                        AtsTokenRow.last_validated_at <= cutoff,
                    )
                )
                .order_by(AtsTokenRow.last_seen_at.desc())
                .limit(max(limit, 1))
            ).all()

    def set_token_validation_result(
        self,
        *,
        provider: str,
        token: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self._session_factory() as session:
            row = session.scalar(
                select(AtsTokenRow).where(
                    and_(AtsTokenRow.provider == provider, AtsTokenRow.token == token)
                )
            )
            if row is None:
                return
            if status == "pending" and row.status == "validated":
                row.status = "validated"
            else:
                row.status = status
            row.last_validated_at = now
            row.last_error = error
            if status == "validated":
                row.validated_at = now
                row.last_error = None
            session.commit()

    def list_validated_tokens_by_provider(self) -> dict[str, list[str]]:
        providers: dict[str, list[str]] = {
            "greenhouse": [],
            "lever": [],
            "smartrecruiters": [],
        }
        with self._session_factory() as session:
            rows = session.scalars(
                select(AtsTokenRow).where(AtsTokenRow.status == "validated")
            ).all()
            for row in rows:
                bucket = providers.setdefault(row.provider, [])
                if row.token not in bucket:
                    bucket.append(row.token)
        for token_list in providers.values():
            token_list.sort()
        return providers

    def create_seed_manifest_build_run(self, *, source_count: int) -> str:
        run_id = str(uuid.uuid4())
        with self._session_factory() as session:
            session.add(
                SeedManifestBuildRunRow(
                    id=run_id,
                    status="running",
                    source_count=max(source_count, 0),
                    discovered_link_count=0,
                    retained_count=0,
                    started_at=utc_now(),
                )
            )
            session.commit()
        return run_id

    def finalize_seed_manifest_build_run(
        self,
        *,
        run_id: str,
        discovered_link_count: int,
        retained_count: int,
        error: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(SeedManifestBuildRunRow, run_id)
            if row is None:
                return
            row.discovered_link_count = max(discovered_link_count, 0)
            row.retained_count = max(retained_count, 0)
            row.status = "failed" if error else "completed"
            row.error = error
            row.completed_at = utc_now()
            session.commit()

    def replace_seed_manifest_entries(
        self,
        *,
        entries: Iterable[tuple[str | None, str, str]],
    ) -> int:
        now = utc_now()
        normalized_entries: dict[str, tuple[str | None, str, str]] = {}
        for company, careers_url, source_page_url in entries:
            careers_url_clean = careers_url.strip()
            source_page_clean = source_page_url.strip()
            if not careers_url_clean or not source_page_clean:
                continue
            normalized_entries[careers_url_clean] = (company, careers_url_clean, source_page_clean)

        with self._session_factory() as session:
            session.execute(
                update(SeedManifestEntryRow).values(is_active=False, updated_at=now)
            )
            existing_rows = session.scalars(
                select(SeedManifestEntryRow).where(
                    SeedManifestEntryRow.careers_url.in_(normalized_entries.keys())
                )
            ).all() if normalized_entries else []
            existing_by_url = {row.careers_url: row for row in existing_rows}

            for company, careers_url, source_page_url in normalized_entries.values():
                normalized_company = (
                    company.strip() if isinstance(company, str) and company.strip() else None
                )
                row = existing_by_url.get(careers_url)
                if row is None:
                    session.add(
                        SeedManifestEntryRow(
                            id=str(uuid.uuid4()),
                            company=normalized_company,
                            careers_url=careers_url,
                            source_page_url=source_page_url,
                            is_active=True,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    continue

                row.company = normalized_company
                row.source_page_url = source_page_url
                row.is_active = True
                row.updated_at = now
            session.commit()
        return len(normalized_entries)

    def list_active_seed_manifest_entries(self, *, limit: int = 20000) -> list[SeedManifestEntryRow]:
        with self._session_factory() as session:
            return session.scalars(
                select(SeedManifestEntryRow)
                .where(SeedManifestEntryRow.is_active.is_(True))
                .order_by(SeedManifestEntryRow.careers_url.asc())
                .limit(max(limit, 1))
            ).all()

    def enqueue_discovery_refresh_request(
        self,
        *,
        requested_by: str | None,
        reason: str | None = None,
    ) -> str:
        request_id = str(uuid.uuid4())
        with self._session_factory() as session:
            session.add(
                DiscoveryRefreshRequestRow(
                    id=request_id,
                    status="queued",
                    requested_by=requested_by,
                    reason=reason,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            session.commit()
        return request_id

    def list_queued_discovery_refresh_ids(self, *, limit: int = 20) -> list[str]:
        with self._session_factory() as session:
            return session.scalars(
                select(DiscoveryRefreshRequestRow.id)
                .where(DiscoveryRefreshRequestRow.status == "queued")
                .order_by(DiscoveryRefreshRequestRow.created_at.asc())
                .limit(max(limit, 1))
            ).all()

    def claim_discovery_refresh_request(self, request_id: str) -> bool:
        now = utc_now()
        with self._session_factory() as session:
            result = session.execute(
                update(DiscoveryRefreshRequestRow)
                .where(
                    and_(
                        DiscoveryRefreshRequestRow.id == request_id,
                        DiscoveryRefreshRequestRow.status == "queued",
                    )
                )
                .values(status="claimed", claimed_at=now, updated_at=now, error=None)
            )
            session.commit()
            return bool(result.rowcount)

    def finalize_discovery_refresh_request(
        self,
        *,
        request_id: str,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self._session_factory() as session:
            row = session.get(DiscoveryRefreshRequestRow, request_id)
            if row is None:
                return
            row.status = "failed" if error else "completed"
            row.error = error
            row.completed_at = now
            row.updated_at = now
            session.commit()
