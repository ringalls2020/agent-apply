from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Protocol
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .cloud_client import CloudAutomationClient
from .db_models import (
    ApplicationAttemptRow,
    ApplicationRecordRow,
    ExternalRunRefRow,
    JobMatchRow,
    ResumeRow,
    UserPreferenceRow,
    UserRow,
    WebhookEventRow,
)
from .models import (
    AgentRunRequest,
    ApplyAttemptCallback,
    ApplyAttemptResult,
    ApplyRunStartRequest,
    ApplyRunStartResponse,
    ApplyRunStatusResponse,
    ApplicationRecord,
    ApplicationStatus,
    CloudApplyRunRequest,
    CloudMatchRunRequest,
    Contact,
    MatchRunStartRequest,
    MatchRunStartResponse,
    MatchRunStatus,
    MatchRunStatusResponse,
    MatchedJob,
    Opportunity,
    PreferenceResponse,
    PreferenceUpsertRequest,
    ResumeResponse,
    ResumeUpsertRequest,
    RunKind,
    UserResponse,
    UserUpsertRequest,
)
from .security import sha256_hex, verify_password

logger = logging.getLogger(__name__)


class ApplicationStore(Protocol):
    def upsert_for_user(self, user_id: str, record: ApplicationRecord) -> ApplicationRecord:
        ...

    def list_for_user(self, user_id: str) -> List[ApplicationRecord]:
        ...

    def list_all(self) -> List[ApplicationRecord]:
        ...


class PostgresStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        logger.debug("postgres_store_initialized")

    def upsert_for_user(
        self, user_id: str, record: ApplicationRecord
    ) -> ApplicationRecord:
        logger.debug(
            "store_upsert_started",
            extra={"record_id": record.id, "status": record.status.value, "user_id": user_id},
        )
        try:
            with self._session_factory() as session:
                row = session.get(ApplicationRecordRow, record.id)

                if row is None:
                    row = ApplicationRecordRow(id=record.id, status=record.status.value)
                    session.add(row)

                self._sync_row(row=row, record=record, user_id=user_id)
                session.commit()
                session.refresh(row)

                stored = self._to_record(row)
        except Exception:
            logger.exception("store_upsert_failed", extra={"record_id": record.id})
            raise

        logger.debug(
            "store_upsert_completed",
            extra={"record_id": stored.id, "status": stored.status.value, "user_id": user_id},
        )
        return stored

    def list_for_user(self, user_id: str) -> List[ApplicationRecord]:
        logger.debug("store_list_for_user_started", extra={"user_id": user_id})
        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(ApplicationRecordRow)
                    .where(ApplicationRecordRow.user_id == user_id)
                    .order_by(ApplicationRecordRow.opportunity_discovered_at.desc())
                ).all()
                records = [self._to_record(row) for row in rows]
        except Exception:
            logger.exception("store_list_for_user_failed", extra={"user_id": user_id})
            raise

        logger.debug(
            "store_list_for_user_completed",
            extra={"records": len(records), "user_id": user_id},
        )
        return records

    def list_all(self) -> List[ApplicationRecord]:
        logger.debug("store_list_all_started")
        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(ApplicationRecordRow).order_by(
                        ApplicationRecordRow.opportunity_discovered_at.desc()
                    )
                ).all()
                records = [self._to_record(row) for row in rows]
        except Exception:
            logger.exception("store_list_all_failed")
            raise

        logger.debug("store_list_all_completed", extra={"records": len(records)})
        return records

    @staticmethod
    def _sync_row(*, row: ApplicationRecordRow, record: ApplicationRecord, user_id: str) -> None:
        row.user_id = user_id
        row.status = record.status.value
        row.opportunity_id = record.opportunity.id
        row.opportunity_title = record.opportunity.title
        row.opportunity_company = record.opportunity.company
        row.opportunity_url = record.opportunity.url
        row.opportunity_reason = record.opportunity.reason
        row.opportunity_discovered_at = record.opportunity.discovered_at
        row.submitted_at = record.submitted_at
        row.notified_at = record.notified_at

        if record.contact is None:
            row.contact_name = None
            row.contact_email = None
            row.contact_role = None
            row.contact_source = None
            return

        row.contact_name = record.contact.name
        row.contact_email = record.contact.email
        row.contact_role = record.contact.role
        row.contact_source = record.contact.source

    @staticmethod
    def _to_record(row: ApplicationRecordRow) -> ApplicationRecord:
        contact = None

        if (
            row.contact_name is not None
            and row.contact_email is not None
            and row.contact_source is not None
        ):
            contact = Contact(
                name=row.contact_name,
                email=row.contact_email,
                role=row.contact_role,
                source=row.contact_source,
            )

        return ApplicationRecord(
            id=row.id,
            opportunity=Opportunity(
                id=row.opportunity_id,
                title=row.opportunity_title,
                company=row.opportunity_company,
                url=row.opportunity_url,
                reason=row.opportunity_reason,
                discovered_at=row.opportunity_discovered_at,
            ),
            status=ApplicationStatus(row.status),
            contact=contact,
            submitted_at=row.submitted_at,
            notified_at=row.notified_at,
        )


class OpportunityAgent:
    """Simple deterministic mock agent for discovery + apply pipeline.

    Replace the mock methods with real providers for:
      - internet search (SerpAPI, Tavily, Bing, etc.)
      - automated applications (Playwright/RPA with guardrails)
      - contact enrichment (Apollo/Clearbit/LinkedIn API)
      - notifications (email/slack/sms)
    """

    def __init__(self, store: ApplicationStore) -> None:
        self.store = store
        logger.debug("opportunity_agent_initialized")

    def run(self, *, user_id: str, request: AgentRunRequest) -> List[ApplicationRecord]:
        logger.info(
            "agent_run_started",
            extra={
                "user_id": user_id,
                "max_opportunities": request.max_opportunities,
                "interest_count": len(request.profile.interests),
            },
        )
        opportunities = self._discover(request)
        records: List[ApplicationRecord] = []

        for idx, opp in enumerate(opportunities, start=1):
            logger.debug(
                "agent_processing_opportunity",
                extra={
                    "step_index": idx,
                    "opportunity_id": opp.id,
                    "company": opp.company,
                },
            )
            applied_record = self._apply(opp)
            enriched_record = self._find_point_of_contact(applied_record)
            notified_record = self._notify(enriched_record)
            records.append(self.store.upsert_for_user(user_id, notified_record))

        logger.info("agent_run_completed", extra={"generated_records": len(records)})
        return records

    def _discover(self, request: AgentRunRequest) -> List[Opportunity]:
        logger.debug(
            "agent_discover_started",
            extra={"max_opportunities": request.max_opportunities},
        )
        interests = ", ".join(request.profile.interests)
        opportunities = []

        for idx in range(request.max_opportunities):
            opportunities.append(
                Opportunity(
                    id=str(uuid4()),
                    title=f"{request.profile.interests[idx % len(request.profile.interests)].title()} Fellow",
                    company=f"Novel Labs {idx + 1}",
                    url=f"https://example.com/jobs/{idx + 1}",
                    reason=(
                        f"Matched resume with interests ({interests}) and found a novel role "
                        "with high skills overlap."
                    ),
                )
            )

        logger.debug(
            "agent_discover_completed", extra={"discovered_count": len(opportunities)}
        )
        return opportunities

    def _apply(self, opportunity: Opportunity) -> ApplicationRecord:
        record = ApplicationRecord(
            id=str(uuid4()),
            opportunity=opportunity,
            status=ApplicationStatus.applied,
            submitted_at=datetime.utcnow(),
        )
        logger.debug(
            "agent_apply_completed",
            extra={
                "application_id": record.id,
                "opportunity_id": opportunity.id,
            },
        )
        return record

    def _find_point_of_contact(self, record: ApplicationRecord) -> ApplicationRecord:
        contact = Contact(
            name=f"Recruiter for {record.opportunity.company}",
            email=f"recruiting@{record.opportunity.company.lower().replace(' ', '')}.com",
            role="Talent Acquisition",
            source="Company careers page",
        )
        record.contact = contact
        logger.debug(
            "agent_contact_enriched",
            extra={
                "application_id": record.id,
                "contact_source": contact.source,
            },
        )
        return record

    def _notify(self, record: ApplicationRecord) -> ApplicationRecord:
        record.status = ApplicationStatus.notified
        record.notified_at = datetime.utcnow()
        logger.debug("agent_notify_completed", extra={"application_id": record.id})
        return record


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class MainPlatformStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        logger.debug("main_platform_store_initialized")

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    def upsert_user(self, user_id: str, payload: UserUpsertRequest) -> UserResponse:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.get(UserRow, user_id)
            if row is None:
                row = UserRow(
                    id=user_id,
                    full_name=payload.full_name.strip(),
                    email=self._normalize_email(payload.email),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.full_name = payload.full_name.strip()
                row.email = self._normalize_email(payload.email)
                row.updated_at = now

            session.commit()
            session.refresh(row)
            return self._to_user(row)

    def get_user_by_email(self, email: str) -> UserResponse | None:
        normalized_email = self._normalize_email(email)
        with self._session_factory() as session:
            row = session.scalar(
                select(UserRow).where(UserRow.email == normalized_email).limit(1)
            )
            if row is None:
                return None
            return self._to_user(row)

    def set_user_password(
        self, *, user_id: str, password_salt: str, password_hash: str
    ) -> None:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.get(UserRow, user_id)
            if row is None:
                raise ValueError("User not found")
            row.password_salt = password_salt
            row.password_hash = password_hash
            row.updated_at = now
            session.commit()

    def verify_user_credentials(self, *, email: str, password: str) -> UserResponse | None:
        normalized_email = self._normalize_email(email)
        with self._session_factory() as session:
            row = session.scalar(
                select(UserRow).where(UserRow.email == normalized_email).limit(1)
            )
            if row is None:
                return None
            if not row.password_salt or not row.password_hash:
                return None
            if not verify_password(password, row.password_salt, row.password_hash):
                return None
            return self._to_user(row)

    def get_user(self, user_id: str) -> UserResponse | None:
        with self._session_factory() as session:
            row = session.get(UserRow, user_id)
            if row is None:
                return None
            return self._to_user(row)

    def upsert_preferences(
        self, user_id: str, payload: PreferenceUpsertRequest
    ) -> PreferenceResponse:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.get(UserPreferenceRow, user_id)
            if row is None:
                row = UserPreferenceRow(
                    user_id=user_id,
                    created_at=now,
                )
                session.add(row)

            row.interests_json = _json_dumps(payload.interests)
            row.locations_json = _json_dumps(payload.locations)
            row.seniority = payload.seniority
            row.applications_per_day = payload.applications_per_day
            row.updated_at = now
            if not row.created_at:
                row.created_at = now

            session.commit()
            session.refresh(row)
            return self._to_preferences(row)

    def get_preferences(self, user_id: str) -> PreferenceResponse | None:
        with self._session_factory() as session:
            row = session.get(UserPreferenceRow, user_id)
            if row is None:
                return None
            return self._to_preferences(row)

    def upsert_resume(self, user_id: str, payload: ResumeUpsertRequest) -> ResumeResponse:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            if row is None:
                row = ResumeRow(
                    id=str(uuid4()),
                    user_id=user_id,
                    filename=payload.filename,
                    resume_text=payload.resume_text,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.filename = payload.filename
                row.resume_text = payload.resume_text
                row.updated_at = now

            session.commit()
            session.refresh(row)
            return self._to_resume(row)

    def get_resume(self, user_id: str) -> ResumeResponse | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            if row is None:
                return None
            return self._to_resume(row)

    def create_external_run_ref(
        self,
        *,
        user_id: str,
        run_type: RunKind,
        external_run_id: str,
        status: MatchRunStatus,
        request_payload: Dict[str, Any],
    ) -> None:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.scalar(
                select(ExternalRunRefRow).where(
                    and_(
                        ExternalRunRefRow.run_type == run_type.value,
                        ExternalRunRefRow.external_run_id == external_run_id,
                    )
                )
            )
            if row is None:
                row = ExternalRunRefRow(
                    id=str(uuid4()),
                    user_id=user_id,
                    run_type=run_type.value,
                    external_run_id=external_run_id,
                    status=status.value,
                    request_payload_json=_json_dumps(request_payload),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.status = status.value
                row.updated_at = now
            session.commit()

    def has_external_run_ref(
        self, *, user_id: str, run_type: RunKind, external_run_id: str
    ) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(ExternalRunRefRow).where(
                    and_(
                        ExternalRunRefRow.user_id == user_id,
                        ExternalRunRefRow.run_type == run_type.value,
                        ExternalRunRefRow.external_run_id == external_run_id,
                    )
                )
            )
            return row is not None

    def update_external_run_ref(
        self,
        *,
        run_type: RunKind,
        external_run_id: str,
        status: MatchRunStatus,
        latest_response: Dict[str, Any],
    ) -> None:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.scalar(
                select(ExternalRunRefRow).where(
                    and_(
                        ExternalRunRefRow.run_type == run_type.value,
                        ExternalRunRefRow.external_run_id == external_run_id,
                    )
                )
            )
            if row is None:
                return
            row.status = status.value
            row.latest_response_json = _json_dumps(latest_response)
            row.updated_at = now
            session.commit()

    def replace_job_matches(
        self, *, user_id: str, external_run_id: str, matches: List[MatchedJob]
    ) -> None:
        with self._session_factory() as session:
            existing = session.scalars(
                select(JobMatchRow).where(
                    and_(
                        JobMatchRow.user_id == user_id,
                        JobMatchRow.external_run_id == external_run_id,
                    )
                )
            ).all()
            for row in existing:
                session.delete(row)

            for match in matches:
                session.add(
                    JobMatchRow(
                        id=str(uuid4()),
                        user_id=user_id,
                        external_run_id=external_run_id,
                        external_job_id=match.external_job_id,
                        title=match.title,
                        company=match.company,
                        location=match.location,
                        apply_url=match.apply_url,
                        source=match.source,
                        reason=match.reason,
                        score=match.score,
                        posted_at=match.posted_at,
                        created_at=datetime.utcnow(),
                    )
                )
            session.commit()

    def list_job_matches(self, *, user_id: str, external_run_id: str) -> List[MatchedJob]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(JobMatchRow).where(
                    and_(
                        JobMatchRow.user_id == user_id,
                        JobMatchRow.external_run_id == external_run_id,
                    )
                )
            ).all()
            return [self._to_matched_job(row) for row in rows]

    def count_apply_attempts_today(self, *, user_id: str) -> int:
        now = datetime.now(timezone.utc)
        window_start = datetime(
            year=now.year,
            month=now.month,
            day=now.day,
            tzinfo=timezone.utc,
        ).replace(tzinfo=None)
        window_end = window_start + timedelta(days=1)

        with self._session_factory() as session:
            count = session.scalar(
                select(func.count())
                .select_from(ApplicationAttemptRow)
                .where(
                    and_(
                        ApplicationAttemptRow.user_id == user_id,
                        ApplicationAttemptRow.created_at >= window_start,
                        ApplicationAttemptRow.created_at < window_end,
                    )
                )
            )
            return int(count or 0)

    def upsert_application_attempt(
        self, *, user_id: str, external_run_id: str, attempt: ApplyAttemptResult
    ) -> None:
        now = datetime.utcnow()
        with self._session_factory() as session:
            row = session.get(ApplicationAttemptRow, attempt.attempt_id)
            if row is None:
                row = ApplicationAttemptRow(
                    id=attempt.attempt_id,
                    user_id=user_id,
                    external_run_id=external_run_id,
                    created_at=now,
                    updated_at=now,
                    job_url=attempt.job_url,
                    status=attempt.status.value,
                    artifacts_json=_json_dumps([]),
                )
                session.add(row)

            row.external_job_id = attempt.external_job_id
            row.job_url = attempt.job_url
            row.status = attempt.status.value
            row.failure_code = (
                attempt.failure_code.value if attempt.failure_code else None
            )
            row.failure_reason = attempt.failure_reason
            row.submitted_at = attempt.submitted_at
            row.artifacts_json = _json_dumps(
                [artifact.model_dump(mode="json") for artifact in attempt.artifacts]
            )
            row.updated_at = now
            session.commit()

    def list_apply_attempts(
        self, *, user_id: str, external_run_id: str
    ) -> List[ApplyAttemptResult]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ApplicationAttemptRow).where(
                    and_(
                        ApplicationAttemptRow.user_id == user_id,
                        ApplicationAttemptRow.external_run_id == external_run_id,
                    )
                )
            ).all()
            return [self._to_apply_attempt(row) for row in rows]

    def webhook_event_exists(self, *, idempotency_key: str) -> bool:
        with self._session_factory() as session:
            row = session.get(WebhookEventRow, idempotency_key)
            return row is not None

    def create_webhook_event(
        self,
        *,
        idempotency_key: str,
        event_type: str,
        external_run_id: str,
        payload_hash: str,
    ) -> bool:
        with self._session_factory() as session:
            row = session.get(WebhookEventRow, idempotency_key)
            if row is not None:
                return False

            session.add(
                WebhookEventRow(
                    idempotency_key=idempotency_key,
                    event_type=event_type,
                    external_run_id=external_run_id,
                    payload_hash=payload_hash,
                    received_at=datetime.utcnow(),
                )
            )
            session.commit()
            return True

    def mark_webhook_event_processed(self, *, idempotency_key: str) -> None:
        with self._session_factory() as session:
            row = session.get(WebhookEventRow, idempotency_key)
            if row is None:
                return
            row.processed_at = datetime.utcnow()
            session.commit()

    @staticmethod
    def _to_user(row: UserRow) -> UserResponse:
        return UserResponse(
            id=row.id,
            full_name=row.full_name,
            email=row.email,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_preferences(row: UserPreferenceRow) -> PreferenceResponse:
        return PreferenceResponse(
            user_id=row.user_id,
            interests=_json_loads(row.interests_json, []),
            locations=_json_loads(row.locations_json, []),
            seniority=row.seniority,
            applications_per_day=row.applications_per_day,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_resume(row: ResumeRow) -> ResumeResponse:
        return ResumeResponse(
            id=row.id,
            user_id=row.user_id,
            filename=row.filename,
            resume_text=row.resume_text,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_matched_job(row: JobMatchRow) -> MatchedJob:
        return MatchedJob(
            external_job_id=row.external_job_id,
            title=row.title,
            company=row.company,
            location=row.location,
            apply_url=row.apply_url,
            source=row.source,
            reason=row.reason,
            score=row.score,
            posted_at=row.posted_at,
        )

    @staticmethod
    def _to_apply_attempt(row: ApplicationAttemptRow) -> ApplyAttemptResult:
        return ApplyAttemptResult(
            attempt_id=row.id,
            external_job_id=row.external_job_id,
            job_url=row.job_url,
            status=row.status,
            failure_code=row.failure_code,
            failure_reason=row.failure_reason,
            submitted_at=row.submitted_at,
            artifacts=_json_loads(row.artifacts_json, []),
        )


class CloudOrchestrationService:
    def __init__(
        self,
        *,
        store: MainPlatformStore,
        cloud_client: CloudAutomationClient,
        default_daily_cap: int = 25,
    ) -> None:
        self.store = store
        self.cloud_client = cloud_client
        self.default_daily_cap = default_daily_cap
        logger.debug(
            "cloud_orchestration_service_initialized",
            extra={"default_daily_cap": default_daily_cap},
        )

    def upsert_user(self, user_id: str, payload: UserUpsertRequest) -> UserResponse:
        user = self.store.upsert_user(user_id=user_id, payload=payload)
        if self.store.get_preferences(user_id) is None:
            self.store.upsert_preferences(
                user_id=user_id,
                payload=PreferenceUpsertRequest(interests=["software"], locations=[]),
            )
        return user

    def upsert_preferences(
        self, user_id: str, payload: PreferenceUpsertRequest
    ) -> PreferenceResponse:
        self._require_user(user_id)
        return self.store.upsert_preferences(user_id=user_id, payload=payload)

    def upsert_resume(self, user_id: str, payload: ResumeUpsertRequest) -> ResumeResponse:
        self._require_user(user_id)
        return self.store.upsert_resume(user_id=user_id, payload=payload)

    def get_user(self, user_id: str) -> UserResponse | None:
        return self.store.get_user(user_id)

    def get_preferences(self, user_id: str) -> PreferenceResponse | None:
        return self.store.get_preferences(user_id)

    def get_resume(self, user_id: str) -> ResumeResponse | None:
        return self.store.get_resume(user_id)

    def start_match_run(
        self, *, user_id: str, payload: MatchRunStartRequest
    ) -> MatchRunStartResponse:
        user, preferences, resume = self._require_user_context(user_id)
        cloud_request = CloudMatchRunRequest(
            user_ref=user_id,
            resume_text=resume.resume_text,
            preferences={
                "interests": preferences.interests,
                "locations": preferences.locations,
                "seniority": preferences.seniority,
            },
            limit=payload.limit,
            location=payload.location
            or (preferences.locations[0] if preferences.locations else None),
            seniority=payload.seniority or preferences.seniority,
        )
        created = self.cloud_client.start_match_run(cloud_request)
        self.store.create_external_run_ref(
            user_id=user_id,
            run_type=RunKind.match,
            external_run_id=created.run_id,
            status=created.status,
            request_payload=cloud_request.model_dump(mode="json"),
        )
        return MatchRunStartResponse(
            run_id=created.run_id,
            status=created.status,
            status_url=f"/v1/users/{user_id}/match-runs/{created.run_id}",
        )

    def get_match_run(self, *, user_id: str, run_id: str) -> MatchRunStatusResponse:
        if not self.store.has_external_run_ref(
            user_id=user_id, run_type=RunKind.match, external_run_id=run_id
        ):
            raise ValueError("Match run not found for user")

        status = self.cloud_client.get_match_run(run_id)
        self.store.update_external_run_ref(
            run_type=RunKind.match,
            external_run_id=run_id,
            status=status.status,
            latest_response=status.model_dump(mode="json"),
        )
        if status.status in {MatchRunStatus.completed, MatchRunStatus.partial}:
            self.store.replace_job_matches(
                user_id=user_id,
                external_run_id=run_id,
                matches=status.matches,
            )
        stored_matches = self.store.list_job_matches(
            user_id=user_id, external_run_id=run_id
        )
        return MatchRunStatusResponse(
            run_id=run_id,
            status=status.status,
            results=stored_matches or status.matches,
            error=status.error,
        )

    def start_apply_run(
        self, *, user_id: str, payload: ApplyRunStartRequest
    ) -> ApplyRunStartResponse:
        user, preferences, resume = self._require_user_context(user_id)
        daily_cap = (
            payload.daily_cap
            if payload.daily_cap is not None
            else preferences.applications_per_day or self.default_daily_cap
        )
        current_count = self.store.count_apply_attempts_today(user_id=user_id)
        if current_count + len(payload.jobs) > daily_cap:
            raise ValueError(
                f"Daily cap exceeded: attempted={len(payload.jobs)} current={current_count} cap={daily_cap}"
            )

        cloud_request = CloudApplyRunRequest(
            user_ref=user_id,
            jobs=payload.jobs,
            profile_payload={
                "full_name": user.full_name,
                "email": user.email,
                "resume_text": resume.resume_text,
                "preferences": preferences.model_dump(mode="json"),
            },
            credentials_ref=payload.credentials_ref,
            daily_cap=daily_cap,
        )
        created = self.cloud_client.start_apply_run(cloud_request)
        self.store.create_external_run_ref(
            user_id=user_id,
            run_type=RunKind.apply,
            external_run_id=created.run_id,
            status=created.status,
            request_payload=cloud_request.model_dump(mode="json"),
        )
        return ApplyRunStartResponse(
            run_id=created.run_id,
            status=created.status,
            status_url=f"/v1/users/{user_id}/apply-runs/{created.run_id}",
        )

    def get_apply_run(self, *, user_id: str, run_id: str) -> ApplyRunStatusResponse:
        if not self.store.has_external_run_ref(
            user_id=user_id, run_type=RunKind.apply, external_run_id=run_id
        ):
            raise ValueError("Apply run not found for user")

        status = self.cloud_client.get_apply_run(run_id)
        self.store.update_external_run_ref(
            run_type=RunKind.apply,
            external_run_id=run_id,
            status=status.status,
            latest_response=status.model_dump(mode="json"),
        )
        for attempt in status.attempts:
            self.store.upsert_application_attempt(
                user_id=user_id,
                external_run_id=run_id,
                attempt=attempt,
            )
        stored_attempts = self.store.list_apply_attempts(
            user_id=user_id, external_run_id=run_id
        )
        return ApplyRunStatusResponse(
            run_id=run_id,
            status=status.status,
            attempts=stored_attempts or status.attempts,
            error=status.error,
        )

    def process_apply_attempt_callback(self, payload: ApplyAttemptCallback) -> None:
        self.store.upsert_application_attempt(
            user_id=payload.user_ref,
            external_run_id=payload.run_id,
            attempt=payload.attempt,
        )

    def register_webhook_event_if_new(
        self,
        *,
        idempotency_key: str,
        event_type: str,
        external_run_id: str,
        raw_body: bytes,
    ) -> bool:
        return self.store.create_webhook_event(
            idempotency_key=idempotency_key,
            event_type=event_type,
            external_run_id=external_run_id,
            payload_hash=sha256_hex(raw_body),
        )

    def mark_webhook_processed(self, *, idempotency_key: str) -> None:
        self.store.mark_webhook_event_processed(idempotency_key=idempotency_key)

    def _require_user(self, user_id: str) -> UserResponse:
        user = self.store.get_user(user_id)
        if user is None:
            raise ValueError("User not found")
        return user

    def _require_user_context(
        self, user_id: str
    ) -> tuple[UserResponse, PreferenceResponse, ResumeResponse]:
        user = self._require_user(user_id)
        preferences = self.store.get_preferences(user_id)
        resume = self.store.get_resume(user_id)
        if preferences is None:
            raise ValueError("User preferences not found")
        if resume is None or not resume.resume_text.strip():
            raise ValueError("User resume not found")
        return user, preferences, resume
