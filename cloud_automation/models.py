from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MatchRunStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    partial = "partial"


class ApplyAttemptStatus(str, Enum):
    queued = "queued"
    browsing = "browsing"
    filling = "filling"
    submitted = "submitted"
    blocked = "blocked"
    failed = "failed"
    succeeded = "succeeded"


class FailureCode(str, Enum):
    captcha_failed = "captcha_failed"
    auth_required = "auth_required"
    form_validation_failed = "form_validation_failed"
    site_blocked = "site_blocked"
    timeout = "timeout"
    unknown = "unknown"


class ArtifactRef(BaseModel):
    kind: str
    url: str
    expires_at: Optional[datetime] = None


class NormalizedJob(BaseModel):
    id: str
    title: str
    company: str
    location: Optional[str] = None
    salary: Optional[str] = None
    apply_url: str
    source: str
    posted_at: Optional[datetime] = None
    description: str


class MatchRunRequest(BaseModel):
    user_ref: str
    resume_text: str
    preferences: Dict[str, Any]
    limit: int = Field(default=25, ge=1, le=100)
    location: Optional[str] = None
    seniority: Optional[str] = None


class MatchRunCreated(BaseModel):
    run_id: str
    status: MatchRunStatus
    status_url: str


class MatchedJob(BaseModel):
    external_job_id: str
    title: str
    company: str
    location: Optional[str] = None
    apply_url: str
    source: str
    reason: str
    score: float = Field(ge=0.0, le=1.0)
    posted_at: Optional[datetime] = None


class MatchRunStatusResponse(BaseModel):
    run_id: str
    status: MatchRunStatus
    matches: List[MatchedJob] = Field(default_factory=list)
    error: Optional[str] = None


class ApplyJob(BaseModel):
    external_job_id: str
    title: Optional[str] = None
    company: Optional[str] = None
    apply_url: str


class ApplyRunRequest(BaseModel):
    user_ref: str
    jobs: List[ApplyJob] = Field(min_length=1)
    profile_payload: Dict[str, Any]
    credentials_ref: Optional[str] = None
    daily_cap: int = Field(default=25, ge=1, le=100)


class ApplyRunCreated(BaseModel):
    run_id: str
    status: MatchRunStatus
    status_url: str


class ApplyAttemptRecord(BaseModel):
    attempt_id: str
    external_job_id: Optional[str] = None
    job_url: str
    status: ApplyAttemptStatus
    failure_code: Optional[FailureCode] = None
    failure_reason: Optional[str] = None
    submitted_at: Optional[datetime] = None
    artifacts: List[ArtifactRef] = Field(default_factory=list)


class ApplyRunStatusResponse(BaseModel):
    run_id: str
    status: MatchRunStatus
    attempts: List[ApplyAttemptRecord] = Field(default_factory=list)
    error: Optional[str] = None


class ApplyAttemptCallbackPayload(BaseModel):
    event_type: str = Field(default="apply.attempt.updated")
    idempotency_key: str
    run_id: str
    user_ref: str
    attempt: ApplyAttemptRecord
    emitted_at: datetime = Field(default_factory=datetime.utcnow)


class JobSearchResponse(BaseModel):
    jobs: List[NormalizedJob]
