from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.time import utc_now

from ..db_models import (
    ApplicationAttemptRow,
    ExternalRunRefRow,
    JobMatchRow,
    ResumeRow,
    UserApplicationProfileRow,
    UserPreferenceRow,
    UserRow,
    WebhookEventRow,
)
from ..models import (
    ApplicationProfileResponse,
    ApplicationProfileUpsertRequest,
    ApplyAttemptResult,
    MatchRunStatus,
    MatchedJob,
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
from ..security import (
    SecurityError,
    decrypt_sensitive_text,
    encrypt_sensitive_text,
    sha256_hex,
    verify_password,
)
from .resume_utils import (
    decode_resume_file_content_base64,
    extract_resume_interests,
    extract_resume_text_from_file,
    sanitize_resume_text,
)

logger = logging.getLogger(__name__)


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

        file_bytes: bytes | None = None
        file_mime_type = _normalize_optional_text(payload.file_mime_type)
        file_size_bytes: int | None = None
        file_sha256: str | None = None
        if payload.file_content_base64 is not None:
            file_bytes = decode_resume_file_content_base64(payload.file_content_base64)
            file_size_bytes = len(file_bytes)
            file_sha256 = sha256_hex(file_bytes)

        raw_resume_text = _normalize_optional_text(payload.resume_text) or ""
        if not raw_resume_text and file_bytes is not None:
            raw_resume_text = extract_resume_text_from_file(
                filename=sanitized_filename,
                file_bytes=file_bytes,
                file_mime_type=file_mime_type,
            )

        sanitized_resume_text = sanitize_resume_text(raw_resume_text)
        if not sanitized_resume_text:
            raise ValueError("Resume text is empty after sanitization")
        parsed_interests = extract_resume_interests(sanitized_resume_text)

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
                    file_bytes=file_bytes,
                    file_mime_type=file_mime_type,
                    file_size_bytes=file_size_bytes,
                    file_sha256=file_sha256,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.filename = sanitized_filename
                row.resume_text = sanitized_resume_text
                row.file_bytes = file_bytes
                row.file_mime_type = file_mime_type
                row.file_size_bytes = file_size_bytes
                row.file_sha256 = file_sha256
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

    def get_resume_file_bundle(self, user_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            if row is None or not row.file_bytes:
                return None
            return {
                "filename": row.filename,
                "mime_type": row.file_mime_type,
                "content_base64": base64.b64encode(row.file_bytes).decode("ascii"),
                "size_bytes": row.file_size_bytes
                if row.file_size_bytes is not None
                else len(row.file_bytes),
                "sha256": row.file_sha256 or sha256_hex(row.file_bytes),
            }

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
        payload = {
            "idempotency_key": idempotency_key,
            "event_type": event_type,
            "external_run_id": external_run_id,
            "payload_hash": payload_hash,
            "received_at": utc_now(),
        }
        with self._session_factory() as session:
            dialect_name = session.bind.dialect.name if session.bind else ""
            if dialect_name == "postgresql":
                result = session.execute(
                    pg_insert(WebhookEventRow)
                    .values(**payload)
                    .on_conflict_do_nothing(
                        index_elements=[WebhookEventRow.idempotency_key]
                    )
                )
                session.commit()
                return bool(result.rowcount)

            if dialect_name == "sqlite":
                result = session.execute(
                    sqlite_insert(WebhookEventRow)
                    .values(**payload)
                    .on_conflict_do_nothing(
                        index_elements=[WebhookEventRow.idempotency_key]
                    )
                )
                session.commit()
                return bool(result.rowcount)

            session.add(WebhookEventRow(**payload))
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False

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
            file_mime_type=row.file_mime_type,
            file_size_bytes=row.file_size_bytes,
            file_sha256=row.file_sha256,
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


__all__ = ["MainPlatformStore"]
