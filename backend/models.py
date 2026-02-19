from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from common.time import utc_now


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    review = "review"
    viewed = "viewed"
    applying = "applying"
    applied = "applied"
    notified = "notified"
    failed = "failed"


class Opportunity(BaseModel):
    id: str
    title: str
    company: str
    location: Optional[str] = None
    url: str
    reason: str
    discovered_at: datetime = Field(default_factory=utc_now)


class Contact(BaseModel):
    name: str
    email: str
    role: Optional[str] = None
    source: str


class ApplicationRecord(BaseModel):
    id: str
    opportunity: Opportunity
    status: ApplicationStatus = ApplicationStatus.discovered
    is_archived: bool = False
    contact: Optional[Contact] = None
    submitted_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None


class ApplicationsSearchResponse(BaseModel):
    applications: List[ApplicationRecord]
    total_count: int
    limit: int
    offset: int


class BulkApplySkippedItem(BaseModel):
    application_id: str
    reason: str
    status: ApplicationStatus | None = None


class BulkApplyResponse(BaseModel):
    run_id: str | None = None
    status_url: str | None = None
    accepted_application_ids: List[str] = Field(default_factory=list)
    skipped: List[BulkApplySkippedItem] = Field(default_factory=list)
    applications: List[ApplicationRecord] = Field(default_factory=list)


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
    manual_review_timeout = "manual_review_timeout"
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


class InferredPreferenceStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    edited = "edited"
    all = "all"


class InferredPreferenceDecision(str, Enum):
    accept = "accept"
    reject = "reject"
    edit = "edit"


class InferredPreferenceItem(BaseModel):
    edge_id: str
    node_id: str
    node_type: str
    canonical_key: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    hard_constraint: bool = False
    rationale: str | None = None
    status: InferredPreferenceStatus = InferredPreferenceStatus.pending
    last_decision_at: datetime | None = None


class InferredPreferenceDecisionInput(BaseModel):
    edge_id: str
    decision: InferredPreferenceDecision
    edited_label: str | None = None


class ConfirmInferredPreferencesResponse(BaseModel):
    accepted_count: int = 0
    rejected_count: int = 0
    edited_count: int = 0
    remaining_pending_count: int = 0
    inferred_preferences: List[InferredPreferenceItem] = Field(default_factory=list)


class EvaluationGateStatus(str, Enum):
    insufficient_data = "INSUFFICIENT_DATA"
    passed = "PASS"
    failed = "FAIL"


class EvaluationGateCheck(BaseModel):
    metric: str
    actual: float
    threshold: float
    comparator: str
    passed: bool


class EvaluationMetricsResponse(BaseModel):
    window_days: int
    impressions: int
    clicks: int
    applications_submitted: int
    precision_at_5: float
    precision_at_10: float
    ndcg_at_10: float
    hard_constraint_violation_rate: float
    ctr: float
    apply_through_rate: float
    gate_status: EvaluationGateStatus
    gate_checks: List[EvaluationGateCheck] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=utc_now)


class CustomAnswerOverride(BaseModel):
    question_key: str = Field(min_length=1, max_length=255)
    answer: str = Field(min_length=1)


class SensitiveProfileUpsertRequest(BaseModel):
    gender: str | None = None
    race_ethnicity: str | None = None
    veteran_status: str | None = None
    disability_status: str | None = None


class SensitiveProfileResponse(BaseModel):
    gender: str = "decline_to_answer"
    race_ethnicity: str = "decline_to_answer"
    veteran_status: str = "decline_to_answer"
    disability_status: str = "decline_to_answer"


class ApplicationProfileUpsertRequest(BaseModel):
    autosubmit_enabled: bool = False
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    work_authorization: str | None = None
    requires_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None
    years_experience: int | None = Field(default=None, ge=0, le=80)
    writing_voice: str | None = None
    cover_letter_style: str | None = None
    achievements_summary: str | None = None
    custom_answers: List[CustomAnswerOverride] = Field(default_factory=list)
    additional_context: str | None = None
    sensitive: SensitiveProfileUpsertRequest | None = None


class ApplicationProfileResponse(BaseModel):
    user_id: str
    autosubmit_enabled: bool
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    work_authorization: str | None = None
    requires_sponsorship: bool | None = None
    willing_to_relocate: bool | None = None
    years_experience: int | None = None
    writing_voice: str | None = None
    cover_letter_style: str | None = None
    achievements_summary: str | None = None
    custom_answers: List[CustomAnswerOverride] = Field(default_factory=list)
    additional_context: str | None = None
    sensitive: SensitiveProfileResponse = Field(default_factory=SensitiveProfileResponse)
    created_at: datetime
    updated_at: datetime


class ResumeUpsertRequest(BaseModel):
    filename: str
    resume_text: str | None = None
    file_content_base64: str | None = None
    file_mime_type: str | None = None


class ResumeResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    resume_text: str
    file_mime_type: str | None = None
    file_size_bytes: int | None = None
    file_sha256: str | None = None
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
    emitted_at: datetime = Field(default_factory=utc_now)


class CallbackAckResponse(BaseModel):
    accepted: bool
    idempotency_key: str
