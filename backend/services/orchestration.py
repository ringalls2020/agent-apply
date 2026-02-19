from __future__ import annotations

import copy
import logging
from typing import Any

from ..cloud_client import CloudAutomationClient
from ..models import (
    ApplicationProfileResponse,
    ApplicationProfileUpsertRequest,
    ApplicationStatus,
    ApplyAttemptCallback,
    ApplyAttemptStatus,
    ApplyRunStartRequest,
    ApplyRunStartResponse,
    ApplyRunStatusResponse,
    CloudApplyRunRequest,
    CloudMatchRunRequest,
    FailureCode,
    MatchRunStartRequest,
    MatchRunStartResponse,
    MatchRunStatus,
    MatchRunStatusResponse,
    PreferenceResponse,
    PreferenceUpsertRequest,
    ResumeResponse,
    ResumeUpsertRequest,
    RunKind,
    SensitiveProfileResponse,
    UserResponse,
    UserUpsertRequest,
)
from ..security import sha256_hex
from .application_store import PostgresStore
from .main_store import MainPlatformStore

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _redact_apply_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
        redacted = copy.deepcopy(payload)
        profile_payload = redacted.get("profile_payload")
        if not isinstance(profile_payload, dict):
            return redacted
        resume_file = profile_payload.get("resume_file")
        if not isinstance(resume_file, dict):
            return redacted
        if "content_base64" in resume_file:
            resume_file["content_base64"] = "<redacted>"
        return redacted

    def start_match_run(
        self, *, user_id: str, payload: MatchRunStartRequest
    ) -> MatchRunStartResponse:
        user, preferences, resume = self._require_user_context(user_id)
        preferred_locations = [
            location.strip()
            for location in preferences.locations
            if isinstance(location, str) and location.strip()
        ]
        default_location_hint = (
            preferred_locations[0] if len(preferred_locations) == 1 else None
        )
        cloud_request = CloudMatchRunRequest(
            user_ref=user_id,
            resume_text=resume.resume_text,
            preferences={
                "interests": preferences.interests,
                "locations": preferences.locations,
                "seniority": preferences.seniority,
            },
            limit=payload.limit,
            location=payload.location or default_location_hint,
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
            status_url="/graphql",
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
        resume_file_payload = self.store.get_resume_file_bundle(user_id)
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

        profile_payload: dict[str, Any] = {
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
        }
        if resume_file_payload is not None:
            profile_payload["resume_file"] = resume_file_payload

        cloud_request = CloudApplyRunRequest(
            user_ref=user_id,
            jobs=payload.jobs,
            profile_payload=profile_payload,
            credentials_ref=payload.credentials_ref,
            daily_cap=daily_cap,
        )
        created = self.cloud_client.start_apply_run(cloud_request)
        request_payload_for_storage = self._redact_apply_request_payload(
            cloud_request.model_dump(mode="json")
        )
        self.store.create_external_run_ref(
            user_id=user_id,
            run_type=RunKind.apply,
            external_run_id=created.run_id,
            status=created.status,
            request_payload=request_payload_for_storage,
        )
        return ApplyRunStartResponse(
            run_id=created.run_id,
            status=created.status,
            status_url="/graphql",
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

        mapped_status = self._map_apply_attempt_to_application_status(
            payload.attempt.status,
            payload.attempt.failure_code,
        )
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
        failure_code: FailureCode | None = None,
    ) -> ApplicationStatus:
        if attempt_status in {ApplyAttemptStatus.succeeded, ApplyAttemptStatus.submitted}:
            return ApplicationStatus.applied
        if attempt_status == ApplyAttemptStatus.blocked:
            if failure_code == FailureCode.manual_review_timeout:
                return ApplicationStatus.review
            return ApplicationStatus.failed
        if attempt_status == ApplyAttemptStatus.failed:
            return ApplicationStatus.failed
        return ApplicationStatus.applying


__all__ = ["CloudOrchestrationService"]
