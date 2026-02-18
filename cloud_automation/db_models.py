from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.time import utc_now

from .db import Base


class JobSourceRow(Base):
    __tablename__ = "job_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    risk_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="high")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    last_cursor: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class RawJobDocumentRow(Base):
    __tablename__ = "raw_job_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("job_sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class DiscoverySeedRow(Base):
    __tablename__ = "discovery_seeds"
    __table_args__ = (
        UniqueConstraint("careers_url"),
        Index("ix_discovery_seeds_domain", "domain"),
        Index("ix_discovery_seeds_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    company: Mapped[str | None] = mapped_column(String(255))
    careers_url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    source_manifest_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class DomainRobotsCacheRow(Base):
    __tablename__ = "domain_robots_cache"
    __table_args__ = (
        Index("ix_domain_robots_cache_expires_at", "expires_at"),
        Index("ix_domain_robots_cache_status", "status"),
    )

    domain: Mapped[str] = mapped_column(String(255), primary_key=True)
    robots_url: Mapped[str] = mapped_column(Text, nullable=False)
    robots_txt: Mapped[str] = mapped_column(Text, nullable=False)
    crawl_delay_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    last_error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)


class AtsTokenRow(Base):
    __tablename__ = "ats_tokens"
    __table_args__ = (
        UniqueConstraint("provider", "token"),
        Index("ix_ats_tokens_provider_status", "provider", "status"),
        Index("ix_ats_tokens_status_seen", "status", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    discovered_method: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)


class AtsTokenEvidenceRow(Base):
    __tablename__ = "ats_token_evidence"
    __table_args__ = (
        UniqueConstraint("token_id", "method", "evidence_url"),
        Index("ix_ats_token_evidence_token_id", "token_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_id: Mapped[str] = mapped_column(String(64), ForeignKey("ats_tokens.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_url: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class NormalizedJobRow(Base):
    __tablename__ = "normalized_jobs"
    __table_args__ = (
        Index("ix_normalized_jobs_created_at", "created_at"),
        Index("ix_normalized_jobs_location", "location"),
        Index("ix_normalized_jobs_source", "source"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    salary: Mapped[str | None] = mapped_column(String(128))
    apply_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class JobFingerprintRow(Base):
    __tablename__ = "job_fingerprints"
    __table_args__ = (UniqueConstraint("fingerprint"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_job_id: Mapped[str] = mapped_column(String(64), ForeignKey("normalized_jobs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class JobIdentityRow(Base):
    __tablename__ = "job_identities"
    __table_args__ = (
        UniqueConstraint("canonical_key"),
        Index("ix_job_identities_canonical_job_id", "canonical_job_id"),
        Index("ix_job_identities_provider_token", "provider", "provider_token"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_job_id: Mapped[str] = mapped_column(String(64), ForeignKey("normalized_jobs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_token: Mapped[str | None] = mapped_column(String(255))
    provider_job_id: Mapped[str | None] = mapped_column(String(128))
    normalized_apply_url_hash: Mapped[str | None] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class CrawlRunRow(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MatchRunRow(Base):
    __tablename__ = "match_runs"
    __table_args__ = (
        Index("ix_match_runs_status_started_at", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class MatchResultRow(Base):
    __tablename__ = "match_results"
    __table_args__ = (UniqueConstraint("run_id", "external_job_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("match_runs.id"), nullable=False)
    external_job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    apply_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)


class ApplyRunRow(Base):
    __tablename__ = "apply_runs"
    __table_args__ = (
        Index("ix_apply_runs_status_started_at", "status", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class ApplyAttemptRow(Base):
    __tablename__ = "apply_attempts"
    __table_args__ = (
        Index("ix_apply_attempts_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("apply_runs.id"), nullable=False)
    external_job_id: Mapped[str | None] = mapped_column(String(64))
    job_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)


class ArtifactRefRow(Base):
    __tablename__ = "artifact_refs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), ForeignKey("apply_attempts.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
