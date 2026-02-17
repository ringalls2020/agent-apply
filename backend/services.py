from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List
from uuid import uuid4

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from common.time import utc_now

from .cloud_client import CloudAutomationClient
from .db_models import (
    ApplicationAttemptRow,
    ApplicationRecordRow,
    ExternalRunRefRow,
    JobMatchRow,
    ResumeRow,
    UserApplicationProfileRow,
    UserPreferenceRow,
    UserRow,
    WebhookEventRow,
)
from .models import (
    ApplicationProfileResponse,
    ApplicationProfileUpsertRequest,
    ApplyAttemptCallback,
    ApplyAttemptStatus,
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
    SensitiveProfileResponse,
    SensitiveProfileUpsertRequest,
    UserResponse,
    UserUpsertRequest,
)
from .security import (
    SecurityError,
    decrypt_sensitive_text,
    encrypt_sensitive_text,
    sha256_hex,
    verify_password,
)

logger = logging.getLogger(__name__)


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

    @classmethod
    def _source_clause(cls, source: str):
        normalized_source = source.strip().lower()
        lower_url = func.lower(func.coalesce(ApplicationRecordRow.opportunity_url, ""))
        if normalized_source == "greenhouse":
            return lower_url.like("%greenhouse%")
        if normalized_source == "lever":
            return lower_url.like("%lever.co%")
        if normalized_source == "smartrecruiters":
            return lower_url.like("%smartrecruiters%")
        if normalized_source == "workday":
            return or_(
                lower_url.like("%myworkdayjobs.com%"),
                lower_url.like("%workday%"),
            )
        if normalized_source == "other":
            return and_(
                not_(lower_url.like("%greenhouse%")),
                not_(lower_url.like("%lever.co%")),
                not_(lower_url.like("%smartrecruiters%")),
                not_(lower_url.like("%myworkdayjobs.com%")),
                not_(lower_url.like("%workday%")),
            )
        return None

    def search_for_user(
        self,
        *,
        user_id: str,
        statuses: list[ApplicationStatus] | None = None,
        q: str | None = None,
        companies: list[str] | None = None,
        sources: list[str] | None = None,
        has_contact: bool | None = None,
        discovered_from: datetime | None = None,
        discovered_to: datetime | None = None,
        sort_by: str = "discovered_at",
        sort_dir: str = "desc",
        limit: int = 25,
        offset: int = 0,
    ) -> tuple[list[ApplicationRecord], int]:
        normalized_limit = min(max(limit, 1), 100)
        normalized_offset = max(offset, 0)

        filters = [ApplicationRecordRow.user_id == user_id]
        if statuses:
            filters.append(ApplicationRecordRow.status.in_([status.value for status in statuses]))

        normalized_query = q.strip().lower() if isinstance(q, str) else ""
        if normalized_query:
            pattern = f"%{normalized_query}%"
            filters.append(
                or_(
                    func.lower(func.coalesce(ApplicationRecordRow.opportunity_title, "")).like(pattern),
                    func.lower(func.coalesce(ApplicationRecordRow.opportunity_company, "")).like(pattern),
                    func.lower(func.coalesce(ApplicationRecordRow.contact_name, "")).like(pattern),
                    func.lower(func.coalesce(ApplicationRecordRow.contact_email, "")).like(pattern),
                )
            )

        normalized_companies = [
            company.strip().lower()
            for company in (companies or [])
            if isinstance(company, str) and company.strip()
        ]
        if normalized_companies:
            filters.append(
                func.lower(ApplicationRecordRow.opportunity_company).in_(normalized_companies)
            )

        source_clauses = []
        for source in (sources or []):
            if not isinstance(source, str):
                continue
            clause = self._source_clause(source)
            if clause is not None:
                source_clauses.append(clause)
        if source_clauses:
            filters.append(or_(*source_clauses))

        if has_contact is True:
            filters.append(
                or_(
                    ApplicationRecordRow.contact_name.is_not(None),
                    ApplicationRecordRow.contact_email.is_not(None),
                )
            )
        elif has_contact is False:
            filters.append(
                and_(
                    ApplicationRecordRow.contact_name.is_(None),
                    ApplicationRecordRow.contact_email.is_(None),
                )
            )

        if discovered_from is not None:
            filters.append(ApplicationRecordRow.opportunity_discovered_at >= discovered_from)
        if discovered_to is not None:
            filters.append(ApplicationRecordRow.opportunity_discovered_at <= discovered_to)

        if sort_by == "company":
            sort_column = func.lower(ApplicationRecordRow.opportunity_company)
        elif sort_by == "status":
            sort_column = func.lower(ApplicationRecordRow.status)
        else:
            sort_column = ApplicationRecordRow.opportunity_discovered_at

        order_by = (
            sort_column.asc()
            if sort_dir.strip().lower() == "asc"
            else sort_column.desc()
        )

        with self._session_factory() as session:
            count_stmt = (
                select(func.count())
                .select_from(ApplicationRecordRow)
                .where(and_(*filters))
            )
            total_count = int(session.scalar(count_stmt) or 0)

            rows = session.scalars(
                select(ApplicationRecordRow)
                .where(and_(*filters))
                .order_by(order_by, ApplicationRecordRow.id.asc())
                .offset(normalized_offset)
                .limit(normalized_limit)
            ).all()
            return [self._to_record(row) for row in rows], total_count

    def get_for_user_by_ids(
        self, *, user_id: str, application_ids: list[str]
    ) -> list[ApplicationRecord]:
        if not application_ids:
            return []
        with self._session_factory() as session:
            rows = session.scalars(
                select(ApplicationRecordRow).where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id.in_(application_ids),
                    )
                )
            ).all()
            row_by_id = {row.id: row for row in rows}
            return [
                self._to_record(row_by_id[application_id])
                for application_id in application_ids
                if application_id in row_by_id
            ]

    def mark_viewed_for_user_application(
        self, *, user_id: str, application_id: str
    ) -> ApplicationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRecordRow)
                .where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id == application_id,
                    )
                )
                .limit(1)
            )
            if row is None:
                return None

            current_status = row.status.strip().lower()
            if current_status == ApplicationStatus.review.value:
                row.status = ApplicationStatus.viewed.value
                session.commit()
                session.refresh(row)
            return self._to_record(row)

    def mark_applied_for_user_application(
        self,
        *,
        user_id: str,
        application_id: str,
        submitted_at: datetime,
    ) -> ApplicationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRecordRow)
                .where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id == application_id,
                    )
                )
                .limit(1)
            )
            if row is None:
                return None

            row.status = ApplicationStatus.applied.value
            row.submitted_at = submitted_at
            session.commit()
            session.refresh(row)
            return self._to_record(row)

    def update_status_for_user_application_ids(
        self,
        *,
        user_id: str,
        application_ids: list[str],
        status: ApplicationStatus,
    ) -> list[ApplicationRecord]:
        if not application_ids:
            return []

        with self._session_factory() as session:
            rows = session.scalars(
                select(ApplicationRecordRow).where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id.in_(application_ids),
                    )
                )
            ).all()
            row_by_id = {row.id: row for row in rows}
            for application_id in application_ids:
                row = row_by_id.get(application_id)
                if row is None:
                    continue
                row.status = status.value

            session.commit()
            for row in rows:
                session.refresh(row)

            return [
                self._to_record(row_by_id[application_id])
                for application_id in application_ids
                if application_id in row_by_id
            ]

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

    def update_status_for_user_opportunity(
        self,
        *,
        user_id: str,
        opportunity_id: str,
        status: ApplicationStatus,
        submitted_at: datetime | None = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRecordRow)
                .where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.opportunity_id == opportunity_id,
                    )
                )
                .limit(1)
            )
            if row is None:
                return

            row.status = status.value
            if submitted_at is not None:
                row.submitted_at = submitted_at
            session.commit()

    @staticmethod
    def _sync_row(*, row: ApplicationRecordRow, record: ApplicationRecord, user_id: str) -> None:
        row.user_id = user_id
        if row.status:
            try:
                existing_status = ApplicationStatus(row.status)
            except Exception:
                existing_status = None
            else:
                if (
                    existing_status in {ApplicationStatus.applied, ApplicationStatus.notified, ApplicationStatus.failed}
                    and record.status in {ApplicationStatus.review, ApplicationStatus.viewed, ApplicationStatus.applying}
                ):
                    row.status = existing_status.value
                else:
                    row.status = record.status.value
        else:
            row.status = record.status.value
        row.opportunity_id = record.opportunity.id
        row.opportunity_title = record.opportunity.title
        row.opportunity_company = record.opportunity.company
        row.opportunity_url = record.opportunity.url
        row.opportunity_reason = record.opportunity.reason
        row.opportunity_discovered_at = record.opportunity.discovered_at
        if record.submitted_at is not None:
            row.submitted_at = record.submitted_at
        if record.notified_at is not None:
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

def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


_DECLINE_TO_ANSWER = "decline_to_answer"


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _sanitize_resume_text(value: str) -> str:
    # PostgreSQL TEXT rejects NUL bytes; binary uploads (PDF/DOCX) may include them.
    without_nul = value.replace("\x00", "")
    normalized = without_nul.replace("\r\n", "\n").replace("\r", "\n")
    scrubbed = "".join(
        char if char in {"\n", "\t"} or ord(char) >= 32 else " "
        for char in normalized
    )
    compacted = re.sub(r"[ \t]+", " ", scrubbed)
    compacted = re.sub(r"\n{3,}", "\n\n", compacted)
    return compacted.strip()


_RESUME_INTEREST_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", (r"\bpython\b",)),
    ("fastapi", (r"\bfastapi\b",)),
    ("sqlalchemy", (r"\bsqlalchemy\b",)),
    ("django", (r"\bdjango\b",)),
    ("flask", (r"\bflask\b",)),
    ("java", (r"\bjava\b",)),
    ("javascript", (r"\bjavascript\b", r"\bjs\b")),
    ("typescript", (r"\btypescript\b", r"\bts\b")),
    ("react", (r"\breact\b",)),
    ("nextjs", (r"\bnext\.?js\b",)),
    ("nodejs", (r"\bnode\.?js\b",)),
    ("graphql", (r"\bgraphql\b",)),
    ("rest-api", (r"\brest(?:ful)?\s+api\b", r"\brest\b")),
    ("sql", (r"\bsql\b",)),
    ("postgresql", (r"\bpostgres(?:ql)?\b",)),
    ("mysql", (r"\bmysql\b",)),
    ("mongodb", (r"\bmongodb\b", r"\bmongo\b")),
    ("redis", (r"\bredis\b",)),
    ("aws", (r"\baws\b", r"\bamazon web services\b")),
    ("gcp", (r"\bgcp\b", r"\bgoogle cloud\b")),
    ("azure", (r"\bazure\b",)),
    ("docker", (r"\bdocker\b",)),
    ("kubernetes", (r"\bkubernetes\b", r"\bk8s\b")),
    ("terraform", (r"\bterraform\b",)),
    ("ci-cd", (r"\bci/cd\b", r"\bci-cd\b", r"\bcontinuous integration\b")),
    ("devops", (r"\bdevops\b",)),
    ("ai", (r"\bartificial intelligence\b", r"\bai\b")),
    ("machine-learning", (r"\bmachine learning\b", r"\bml\b")),
    ("deep-learning", (r"\bdeep learning\b",)),
    ("nlp", (r"\bnlp\b", r"\bnatural language processing\b")),
    ("llm", (r"\bllm(?:s)?\b", r"\blarge language model(?:s)?\b")),
    ("data-science", (r"\bdata science\b", r"\bdata scientist\b")),
    ("data-engineering", (r"\bdata engineering\b", r"\bdata engineer\b")),
    ("mlops", (r"\bmlops\b",)),
    ("automation", (r"\bautomation\b",)),
    ("backend", (r"\bbackend\b", r"\bback-end\b")),
    ("frontend", (r"\bfrontend\b", r"\bfront-end\b")),
    ("full-stack", (r"\bfull stack\b", r"\bfull-stack\b")),
    ("security", (r"\bsecurity\b", r"\bcybersecurity\b")),
    ("robotics", (r"\brobotics\b",)),
    ("climate", (r"\bclimate\b",)),
)

_RESUME_INTEREST_ALIAS_TO_CANONICAL: dict[str, str] = {
    "machine learning": "machine-learning",
    "artificial intelligence": "ai",
    "google cloud": "gcp",
    "amazon web services": "aws",
    "rest api": "rest-api",
    "full stack": "full-stack",
    "next.js": "nextjs",
    "node.js": "nodejs",
}

_RESUME_NOISE_TOKENS = {
    "skills",
    "interests",
    "experience",
    "project",
    "projects",
    "summary",
    "professional",
    "engineer",
    "developer",
    "team",
    "work",
}


def _normalize_interest_token(token: str) -> str:
    normalized = token.strip().lower()
    normalized = re.sub(r"[^a-z0-9+#.\- ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    if normalized in _RESUME_INTEREST_ALIAS_TO_CANONICAL:
        return _RESUME_INTEREST_ALIAS_TO_CANONICAL[normalized]
    return normalized.replace(" ", "-")


def _extract_resume_interests(resume_text: str, *, max_items: int = 15) -> list[str]:
    normalized = resume_text.lower()
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()

    for canonical, patterns in _RESUME_INTEREST_PATTERNS:
        first_index: int | None = None
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match is not None:
                first_index = match.start() if first_index is None else min(first_index, match.start())
        if first_index is None:
            continue
        ranked.append((first_index, canonical))
        seen.add(canonical)

    for section_match in re.finditer(
        r"(?:skills?|interests?)\s*[:\-]\s*([^\n]{1,300})",
        normalized,
    ):
        segment = section_match.group(1)
        for candidate in re.split(r"[,/;|]", segment):
            normalized_token = _normalize_interest_token(candidate)
            if (
                not normalized_token
                or normalized_token in seen
                or normalized_token in _RESUME_NOISE_TOKENS
                or len(normalized_token) < 2
                or len(normalized_token) > 40
            ):
                continue
            ranked.append((section_match.start(), normalized_token))
            seen.add(normalized_token)

    ranked.sort(key=lambda entry: entry[0])
    return [interest for _, interest in ranked[:max_items]]


class MainPlatformStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        logger.debug("main_platform_store_initialized")

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    def upsert_user(self, user_id: str, payload: UserUpsertRequest) -> UserResponse:
        now = utc_now()
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
        now = utc_now()
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
        now = utc_now()
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

    def upsert_application_profile(
        self, user_id: str, payload: ApplicationProfileUpsertRequest
    ) -> ApplicationProfileResponse:
        now = utc_now()
        with self._session_factory() as session:
            row = session.get(UserApplicationProfileRow, user_id)
            if row is None:
                row = UserApplicationProfileRow(
                    user_id=user_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)

            row.autosubmit_enabled = payload.autosubmit_enabled
            row.phone = _normalize_optional_text(payload.phone)
            row.city = _normalize_optional_text(payload.city)
            row.state = _normalize_optional_text(payload.state)
            row.country = _normalize_optional_text(payload.country)

            row.linkedin_url = _normalize_optional_text(payload.linkedin_url)
            row.github_url = _normalize_optional_text(payload.github_url)
            row.portfolio_url = _normalize_optional_text(payload.portfolio_url)

            row.work_authorization = _normalize_optional_text(payload.work_authorization)
            row.requires_sponsorship = payload.requires_sponsorship
            row.willing_to_relocate = payload.willing_to_relocate
            row.years_experience = payload.years_experience

            row.writing_voice = _normalize_optional_text(payload.writing_voice)
            row.cover_letter_style = _normalize_optional_text(payload.cover_letter_style)
            row.achievements_summary = _normalize_optional_text(payload.achievements_summary)
            row.additional_context = _normalize_optional_text(payload.additional_context)
            row.custom_answers_json = _json_dumps(
                [item.model_dump(mode="json") for item in payload.custom_answers]
            )

            sensitive = payload.sensitive or SensitiveProfileUpsertRequest()
            row.gender_encrypted = self._encrypt_optional_sensitive(sensitive.gender)
            row.race_ethnicity_encrypted = self._encrypt_optional_sensitive(
                sensitive.race_ethnicity
            )
            row.veteran_status_encrypted = self._encrypt_optional_sensitive(
                sensitive.veteran_status
            )
            row.disability_status_encrypted = self._encrypt_optional_sensitive(
                sensitive.disability_status
            )

            row.updated_at = now
            if not row.created_at:
                row.created_at = now

            session.commit()
            session.refresh(row)
            return self._to_application_profile(row)

    def get_application_profile(self, user_id: str) -> ApplicationProfileResponse | None:
        with self._session_factory() as session:
            row = session.get(UserApplicationProfileRow, user_id)
            if row is None:
                return None
            return self._to_application_profile(row)

    def upsert_resume(self, user_id: str, payload: ResumeUpsertRequest) -> ResumeResponse:
        now = utc_now()
        sanitized_filename = payload.filename.replace("\x00", "").strip()
        if not sanitized_filename:
            raise ValueError("Resume filename is required")

        sanitized_resume_text = _sanitize_resume_text(payload.resume_text)
        if not sanitized_resume_text:
            raise ValueError("Resume text is empty after sanitization")
        parsed_interests = _extract_resume_interests(sanitized_resume_text)

        with self._session_factory() as session:
            row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            if row is None:
                row = ResumeRow(
                    id=str(uuid4()),
                    user_id=user_id,
                    filename=sanitized_filename,
                    resume_text=sanitized_resume_text,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.filename = sanitized_filename
                row.resume_text = sanitized_resume_text
                row.updated_at = now

            if parsed_interests:
                preferences_row = session.get(UserPreferenceRow, user_id)
                if preferences_row is None:
                    preferences_row = UserPreferenceRow(
                        user_id=user_id,
                        interests_json=_json_dumps(parsed_interests),
                        locations_json=_json_dumps([]),
                        applications_per_day=25,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(preferences_row)
                else:
                    preferences_row.interests_json = _json_dumps(parsed_interests)
                    preferences_row.updated_at = now

            session.commit()
            session.refresh(row)
            if parsed_interests:
                logger.info(
                    "resume_interests_parsed",
                    extra={"user_id": user_id, "interest_count": len(parsed_interests)},
                )
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
        now = utc_now()
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
        now = utc_now()
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
                        created_at=utc_now(),
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
        now = utc_now()
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
                    received_at=utc_now(),
                )
            )
            session.commit()
            return True

    def mark_webhook_event_processed(self, *, idempotency_key: str) -> None:
        with self._session_factory() as session:
            row = session.get(WebhookEventRow, idempotency_key)
            if row is None:
                return
            row.processed_at = utc_now()
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
    def _encrypt_optional_sensitive(value: str | None) -> str | None:
        normalized = _normalize_optional_text(value)
        if normalized is None:
            return None
        return encrypt_sensitive_text(normalized)

    @staticmethod
    def _decrypt_sensitive_with_default(value: str | None) -> str:
        if not value:
            return _DECLINE_TO_ANSWER
        try:
            decrypted = decrypt_sensitive_text(value).strip()
            return decrypted or _DECLINE_TO_ANSWER
        except SecurityError:
            logger.warning("sensitive_profile_field_decrypt_failed")
            return _DECLINE_TO_ANSWER

    @staticmethod
    def _parse_custom_answers(raw_json: str | None) -> list[dict[str, str]]:
        decoded = _json_loads(raw_json, [])
        if isinstance(decoded, list):
            return [
                {"question_key": str(item.get("question_key", "")), "answer": str(item.get("answer", ""))}
                for item in decoded
                if isinstance(item, dict)
                and str(item.get("question_key", "")).strip()
                and str(item.get("answer", "")).strip()
            ]
        if isinstance(decoded, dict):
            return [
                {"question_key": str(key), "answer": str(value)}
                for key, value in decoded.items()
                if str(key).strip() and str(value).strip()
            ]
        return []

    @classmethod
    def _to_application_profile(
        cls, row: UserApplicationProfileRow
    ) -> ApplicationProfileResponse:
        return ApplicationProfileResponse(
            user_id=row.user_id,
            autosubmit_enabled=row.autosubmit_enabled,
            phone=row.phone,
            city=row.city,
            state=row.state,
            country=row.country,
            linkedin_url=row.linkedin_url,
            github_url=row.github_url,
            portfolio_url=row.portfolio_url,
            work_authorization=row.work_authorization,
            requires_sponsorship=row.requires_sponsorship,
            willing_to_relocate=row.willing_to_relocate,
            years_experience=row.years_experience,
            writing_voice=row.writing_voice,
            cover_letter_style=row.cover_letter_style,
            achievements_summary=row.achievements_summary,
            custom_answers=cls._parse_custom_answers(row.custom_answers_json),
            additional_context=row.additional_context,
            sensitive=SensitiveProfileResponse(
                gender=cls._decrypt_sensitive_with_default(row.gender_encrypted),
                race_ethnicity=cls._decrypt_sensitive_with_default(row.race_ethnicity_encrypted),
                veteran_status=cls._decrypt_sensitive_with_default(row.veteran_status_encrypted),
                disability_status=cls._decrypt_sensitive_with_default(row.disability_status_encrypted),
            ),
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
        application_store: PostgresStore | None = None,
        default_daily_cap: int = 25,
    ) -> None:
        self.store = store
        self.cloud_client = cloud_client
        self.application_store = application_store
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

    def upsert_application_profile(
        self, user_id: str, payload: ApplicationProfileUpsertRequest
    ) -> ApplicationProfileResponse:
        self._require_user(user_id)
        return self.store.upsert_application_profile(user_id=user_id, payload=payload)

    def get_user(self, user_id: str) -> UserResponse | None:
        return self.store.get_user(user_id)

    def get_preferences(self, user_id: str) -> PreferenceResponse | None:
        return self.store.get_preferences(user_id)

    def get_resume(self, user_id: str) -> ResumeResponse | None:
        return self.store.get_resume(user_id)

    def get_application_profile(self, user_id: str) -> ApplicationProfileResponse | None:
        return self.store.get_application_profile(user_id)

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
        application_profile = self.store.get_application_profile(user_id)
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
                "application_profile": (
                    application_profile.model_dump(mode="json")
                    if application_profile is not None
                    else {
                        "user_id": user_id,
                        "autosubmit_enabled": False,
                        "custom_answers": [],
                        "sensitive": SensitiveProfileResponse().model_dump(mode="json"),
                    }
                ),
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
        if (
            self.application_store is None
            or not payload.attempt.external_job_id
        ):
            return

        mapped_status = self._map_apply_attempt_to_application_status(payload.attempt.status)
        self.application_store.update_status_for_user_opportunity(
            user_id=payload.user_ref,
            opportunity_id=payload.attempt.external_job_id,
            status=mapped_status,
            submitted_at=payload.attempt.submitted_at,
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

    @staticmethod
    def _map_apply_attempt_to_application_status(
        attempt_status: ApplyAttemptStatus,
    ) -> ApplicationStatus:
        if attempt_status in {ApplyAttemptStatus.succeeded, ApplyAttemptStatus.submitted}:
            return ApplicationStatus.applied
        if attempt_status in {ApplyAttemptStatus.failed, ApplyAttemptStatus.blocked}:
            return ApplicationStatus.failed
        return ApplicationStatus.applying
