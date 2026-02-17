from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    applied = "applied"
    notified = "notified"


class Opportunity(BaseModel):
    id: str
    title: str
    company: str
    url: str
    reason: str
    discovered_at: datetime = Field(default_factory=datetime.utcnow)


class Contact(BaseModel):
    name: str
    email: str
    role: Optional[str] = None
    source: str


class ApplicationRecord(BaseModel):
    id: str
    opportunity: Opportunity
    status: ApplicationStatus = ApplicationStatus.discovered
    contact: Optional[Contact] = None
    submitted_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None


class CandidateProfile(BaseModel):
    full_name: str
    email: str
    resume_text: str
    interests: List[str] = Field(min_length=1)


class AgentRunRequest(BaseModel):
    profile: CandidateProfile
    max_opportunities: int = Field(default=5, ge=1, le=25)


class AgentRunResponse(BaseModel):
    applications: List[ApplicationRecord]


class RunKind(str, Enum):
    match = "match"
    apply = "apply"


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


class UserUpsertRequest(BaseModel):
    full_name: str
    email: str


class UserResponse(BaseModel):
    id: str
    full_name: str
    email: str
    created_at: datetime
    updated_at: datetime


class AuthSignupRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class AuthUserProfile(BaseModel):
    id: str
    full_name: str
    email: str
    interests: List[str] = Field(default_factory=list)
    applications_per_day: int = 25
    resume_filename: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: AuthUserProfile


class PreferenceUpsertRequest(BaseModel):
    interests: List[str] = Field(min_length=1)
    locations: List[str] = Field(default_factory=list)
    seniority: Optional[str] = None
    applications_per_day: int = Field(default=25, ge=1, le=100)


class PreferenceResponse(BaseModel):
    user_id: str
    interests: List[str]
    locations: List[str]
    seniority: Optional[str] = None
    applications_per_day: int
    created_at: datetime
    updated_at: datetime


class ResumeUpsertRequest(BaseModel):
    filename: str
    resume_text: str


class ResumeResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    resume_text: str
    updated_at: datetime


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


class MatchRunStartRequest(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)
    location: Optional[str] = None
    seniority: Optional[str] = None


class MatchRunStartResponse(BaseModel):
    run_id: str
    run_type: RunKind = RunKind.match
    status: MatchRunStatus
    status_url: str


class MatchRunStatusResponse(BaseModel):
    run_id: str
    run_type: RunKind = RunKind.match
    status: MatchRunStatus
    results: List[MatchedJob] = Field(default_factory=list)
    error: Optional[str] = None


class ApplyTargetJob(BaseModel):
    external_job_id: str
    title: Optional[str] = None
    company: Optional[str] = None
    apply_url: str


class ApplyRunStartRequest(BaseModel):
    jobs: List[ApplyTargetJob] = Field(min_length=1)
    credentials_ref: Optional[str] = None
    daily_cap: Optional[int] = Field(default=None, ge=1, le=100)


class ArtifactRef(BaseModel):
    kind: str
    url: str
    expires_at: Optional[datetime] = None


class ApplyAttemptResult(BaseModel):
    attempt_id: str
    external_job_id: Optional[str] = None
    job_url: str
    status: ApplyAttemptStatus
    failure_code: Optional[FailureCode] = None
    failure_reason: Optional[str] = None
    submitted_at: Optional[datetime] = None
    artifacts: List[ArtifactRef] = Field(default_factory=list)


class ApplyRunStartResponse(BaseModel):
    run_id: str
    run_type: RunKind = RunKind.apply
    status: MatchRunStatus
    status_url: str


class ApplyRunStatusResponse(BaseModel):
    run_id: str
    run_type: RunKind = RunKind.apply
    status: MatchRunStatus
    attempts: List[ApplyAttemptResult] = Field(default_factory=list)
    error: Optional[str] = None


class CloudMatchRunRequest(BaseModel):
    user_ref: str
    resume_text: str
    preferences: Dict[str, Any]
    limit: int = Field(default=25, ge=1, le=100)
    location: Optional[str] = None
    seniority: Optional[str] = None


class CloudMatchRunCreated(BaseModel):
    run_id: str
    status: MatchRunStatus
    status_url: str


class CloudMatchRunStatus(BaseModel):
    run_id: str
    status: MatchRunStatus
    matches: List[MatchedJob] = Field(default_factory=list)
    error: Optional[str] = None


class CloudApplyRunRequest(BaseModel):
    user_ref: str
    jobs: List[ApplyTargetJob] = Field(min_length=1)
    profile_payload: Dict[str, Any]
    credentials_ref: Optional[str] = None
    daily_cap: int = Field(default=25, ge=1, le=100)


class CloudApplyRunCreated(BaseModel):
    run_id: str
    status: MatchRunStatus
    status_url: str


class CloudApplyRunStatus(BaseModel):
    run_id: str
    status: MatchRunStatus
    attempts: List[ApplyAttemptResult] = Field(default_factory=list)
    error: Optional[str] = None


class ApplyAttemptCallback(BaseModel):
    event_type: str = Field(default="apply.attempt.updated")
    idempotency_key: str
    run_id: str
    attempt: ApplyAttemptResult
    user_ref: str
    emitted_at: datetime = Field(default_factory=datetime.utcnow)


class CallbackAckResponse(BaseModel):
    accepted: bool
    idempotency_key: str
