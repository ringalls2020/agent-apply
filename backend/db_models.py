from datetime import datetime

from sqlalchemy import (
    Boolean,
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

from .db import Base


class ApplicationRecordRow(Base):
    __tablename__ = "applications"
    __table_args__ = (
        Index("ix_applications_user_discovered_at", "user_id", "opportunity_discovered_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    opportunity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    opportunity_title: Mapped[str] = mapped_column(String(255), nullable=False)
    opportunity_company: Mapped[str] = mapped_column(String(255), nullable=False)
    opportunity_url: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity_reason: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity_discovered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )

    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_role: Mapped[str | None] = mapped_column(String(255))
    contact_source: Mapped[str | None] = mapped_column(String(255))

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_salt: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class UserPreferenceRow(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    interests_json: Mapped[str] = mapped_column(Text, nullable=False)
    locations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    seniority: Mapped[str | None] = mapped_column(String(64))
    applications_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class UserApplicationProfileRow(Base):
    __tablename__ = "user_application_profiles"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), primary_key=True
    )
    autosubmit_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    phone: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str | None] = mapped_column(String(128))

    linkedin_url: Mapped[str | None] = mapped_column(Text)
    github_url: Mapped[str | None] = mapped_column(Text)
    portfolio_url: Mapped[str | None] = mapped_column(Text)

    work_authorization: Mapped[str | None] = mapped_column(String(128))
    requires_sponsorship: Mapped[bool | None] = mapped_column(Boolean)
    willing_to_relocate: Mapped[bool | None] = mapped_column(Boolean)
    years_experience: Mapped[int | None] = mapped_column(Integer)

    writing_voice: Mapped[str | None] = mapped_column(String(64))
    cover_letter_style: Mapped[str | None] = mapped_column(String(64))
    achievements_summary: Mapped[str | None] = mapped_column(Text)
    custom_answers_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    additional_context: Mapped[str | None] = mapped_column(Text)

    gender_encrypted: Mapped[str | None] = mapped_column(Text)
    race_ethnicity_encrypted: Mapped[str | None] = mapped_column(Text)
    veteran_status_encrypted: Mapped[str | None] = mapped_column(Text)
    disability_status_encrypted: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class ResumeRow(Base):
    __tablename__ = "resumes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, unique=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    resume_text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class ExternalRunRefRow(Base):
    __tablename__ = "external_run_refs"
    __table_args__ = (UniqueConstraint("run_type", "external_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    run_type: Mapped[str] = mapped_column(String(16), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    latest_response_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class JobMatchRow(Base):
    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint("user_id", "external_run_id", "external_job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_job_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255))
    apply_url: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class ApplicationAttemptRow(Base):
    __tablename__ = "application_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_job_id: Mapped[str | None] = mapped_column(String(128))
    job_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    artifacts_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class WebhookEventRow(Base):
    __tablename__ = "webhook_events"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
