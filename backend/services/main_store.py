from __future__ import annotations

import base64
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Sequence
from uuid import uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.time import utc_now

from ..db_models import (
    ApplicationAttemptRow,
    EvaluationMetricSnapshotRow,
    ExternalRunRefRow,
    JobMatchRow,
    JobMatchExplanationRow,
    PreferenceEdgeRow,
    PreferenceEvidenceRow,
    PreferenceFeedbackRow,
    PreferenceNodeRow,
    PreferenceProfileRow,
    RecommendationEventRow,
    RecommendationImpressionRow,
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
    ConfirmInferredPreferencesResponse,
    EvaluationGateCheck,
    EvaluationGateStatus,
    EvaluationMetricsResponse,
    InferredPreferenceDecision,
    InferredPreferenceDecisionInput,
    InferredPreferenceItem,
    InferredPreferenceStatus,
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
    normalize_interest_token,
    sanitize_resume_text,
)
from .preference_graph import (
    GraphPreferenceEdge,
    PreferenceHypothesis,
    build_sparse_semantic_vector,
    evaluate_match_with_graph,
    extract_resume_preference_hypotheses,
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


_PREFERENCE_GRAPH_EXTRACTOR_VERSION = "resume-pref-graph-v1"
_GRAPH_RE_RANK_REASON_PREFIX = "graph-hybrid"
_DEFAULT_EVALUATION_GATE_THRESHOLDS = {
    "min_impressions": 50.0,
    "min_runs": 10.0,
    "precision_at_5_min": 0.35,
    "precision_at_10_min": 0.25,
    "ndcg_at_10_min": 0.45,
    "hard_constraint_violation_max": 0.01,
    "ctr_min": 0.10,
    "apply_through_min": 0.03,
}
_RECOMMENDATION_CLICK_EVENT_TYPES = {
    "application_viewed",
}
_RECOMMENDATION_SUBMITTED_EVENT_TYPES = {
    "application_applied",
    "application_submitted",
    "application_submitted_callback",
}


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

            resume_row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            try:
                self._synthesize_preference_profile(
                    session=session,
                    user_id=user_id,
                    preferences_row=row,
                    resume_row=resume_row,
                    now=now,
                    trigger_source="manual",
                )
            except Exception:
                logger.exception(
                    "preference_graph_dual_write_failed",
                    extra={"user_id": user_id, "source": "manual"},
                )

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

            resume_sha = self._resume_fingerprint(row)
            filtered_parsed_interests = self._filter_rejected_resume_interests(
                session=session,
                user_id=user_id,
                resume_sha256=resume_sha,
                parsed_interests=parsed_interests,
            )

            if filtered_parsed_interests:
                preferences_row = session.get(UserPreferenceRow, user_id)
                if preferences_row is None:
                    preferences_row = UserPreferenceRow(
                        user_id=user_id,
                        interests_json=_json_dumps(filtered_parsed_interests),
                        locations_json=_json_dumps([]),
                        applications_per_day=25,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(preferences_row)
                else:
                    preferences_row.interests_json = _json_dumps(filtered_parsed_interests)
                    preferences_row.updated_at = now

            preferences_for_profile = (
                preferences_row
                if filtered_parsed_interests
                else session.get(UserPreferenceRow, user_id)
            )
            try:
                self._synthesize_preference_profile(
                    session=session,
                    user_id=user_id,
                    preferences_row=preferences_for_profile,
                    resume_row=row,
                    now=now,
                    trigger_source="resume_parse",
                )
            except Exception:
                logger.exception(
                    "preference_graph_dual_write_failed",
                    extra={"user_id": user_id, "source": "resume_parse"},
                )

            session.commit()
            session.refresh(row)
            if filtered_parsed_interests:
                logger.info(
                    "resume_interests_parsed",
                    extra={
                        "user_id": user_id,
                        "interest_count": len(filtered_parsed_interests),
                    },
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

    def list_inferred_preferences(
        self,
        *,
        user_id: str,
        status_filter: InferredPreferenceStatus = InferredPreferenceStatus.pending,
    ) -> List[InferredPreferenceItem]:
        with self._session_factory() as session:
            profile = self._active_preference_profile(session=session, user_id=user_id)
            if profile is None:
                return []

            resume_row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            resume_sha = self._resume_fingerprint(resume_row)
            resume_id = resume_row.id if resume_row is not None else None

            edge_rows = session.scalars(
                select(PreferenceEdgeRow).where(
                    and_(
                        PreferenceEdgeRow.profile_id == profile.id,
                        PreferenceEdgeRow.source == "resume_parse",
                    )
                )
            ).all()
            if not edge_rows:
                return []

            node_ids = [edge.node_id for edge in edge_rows]
            nodes = session.scalars(
                select(PreferenceNodeRow).where(PreferenceNodeRow.id.in_(node_ids))
            ).all()
            nodes_by_id = {node.id: node for node in nodes}

            evidence_rows = session.scalars(
                select(PreferenceEvidenceRow).where(
                    and_(
                        PreferenceEvidenceRow.user_id == user_id,
                        PreferenceEvidenceRow.node_id.in_(node_ids),
                        PreferenceEvidenceRow.resume_id == resume_id
                        if resume_id is not None
                        else PreferenceEvidenceRow.resume_id.is_(None),
                    )
                )
            ).all()
            evidence_by_node: dict[str, PreferenceEvidenceRow] = {}
            for evidence in evidence_rows:
                existing = evidence_by_node.get(evidence.node_id)
                if existing is None or evidence.created_at > existing.created_at:
                    evidence_by_node[evidence.node_id] = evidence

            latest_feedback_by_key = self._latest_feedback_by_node_key(
                session=session,
                user_id=user_id,
                resume_sha256=resume_sha,
            )

            items: list[InferredPreferenceItem] = []
            for edge in edge_rows:
                node = nodes_by_id.get(edge.node_id)
                if node is None or node.node_type not in {"skill", "location"}:
                    continue
                feedback = latest_feedback_by_key.get((node.node_type, node.canonical_key))
                status = self._status_from_feedback_decision(
                    feedback.decision if feedback is not None else None
                )
                if status_filter != InferredPreferenceStatus.all and status != status_filter:
                    continue
                label = node.label
                if feedback is not None and feedback.decision == InferredPreferenceDecision.edit.value:
                    edited_label = _json_loads(feedback.detail_json, {}).get("edited_label")
                    if isinstance(edited_label, str) and edited_label.strip():
                        label = edited_label.strip()

                rationale: str | None = None
                evidence = evidence_by_node.get(node.id)
                if evidence is not None and isinstance(evidence.rationale, str):
                    rationale = evidence.rationale

                items.append(
                    InferredPreferenceItem(
                        edge_id=edge.id,
                        node_id=node.id,
                        node_type=node.node_type,
                        canonical_key=node.canonical_key,
                        label=label,
                        confidence=edge.confidence,
                        weight=edge.weight,
                        hard_constraint=edge.hard_constraint,
                        rationale=rationale,
                        status=status,
                        last_decision_at=feedback.created_at if feedback is not None else None,
                    )
                )

            items.sort(
                key=lambda item: (
                    item.node_type,
                    item.label.lower(),
                    -item.confidence,
                    -item.weight,
                )
            )
            return items

    def confirm_inferred_preferences(
        self,
        *,
        user_id: str,
        actions: Sequence[InferredPreferenceDecisionInput],
    ) -> ConfirmInferredPreferencesResponse:
        if not actions:
            pending = self.list_inferred_preferences(
                user_id=user_id,
                status_filter=InferredPreferenceStatus.pending,
            )
            return ConfirmInferredPreferencesResponse(
                accepted_count=0,
                rejected_count=0,
                edited_count=0,
                remaining_pending_count=len(pending),
                inferred_preferences=pending,
            )

        now = utc_now()
        accepted_count = 0
        rejected_count = 0
        edited_count = 0

        with self._session_factory() as session:
            profile = self._active_preference_profile(session=session, user_id=user_id)
            if profile is None:
                raise ValueError("No active preference profile found")

            resume_row = session.scalar(
                select(ResumeRow).where(ResumeRow.user_id == user_id).limit(1)
            )
            resume_sha = self._resume_fingerprint(resume_row)

            unique_edge_ids = sorted({action.edge_id for action in actions})
            edge_rows = session.scalars(
                select(PreferenceEdgeRow).where(
                    and_(
                        PreferenceEdgeRow.user_id == user_id,
                        PreferenceEdgeRow.profile_id == profile.id,
                        PreferenceEdgeRow.source == "resume_parse",
                        PreferenceEdgeRow.id.in_(unique_edge_ids),
                    )
                )
            ).all()
            edges_by_id = {edge.id: edge for edge in edge_rows}
            missing_edge_ids = [edge_id for edge_id in unique_edge_ids if edge_id not in edges_by_id]
            if missing_edge_ids:
                raise ValueError("One or more inferred preference edges are invalid")

            node_rows = session.scalars(
                select(PreferenceNodeRow).where(
                    PreferenceNodeRow.id.in_([edge.node_id for edge in edge_rows])
                )
            ).all()
            nodes_by_id = {node.id: node for node in node_rows}

            preferences_row = session.get(UserPreferenceRow, user_id)
            if preferences_row is None:
                preferences_row = UserPreferenceRow(
                    user_id=user_id,
                    interests_json=_json_dumps([]),
                    locations_json=_json_dumps([]),
                    applications_per_day=25,
                    created_at=now,
                    updated_at=now,
                )
                session.add(preferences_row)

            interests = [
                str(item).strip()
                for item in _json_loads(preferences_row.interests_json, [])
                if isinstance(item, str) and item.strip()
            ]
            locations = [
                str(item).strip()
                for item in _json_loads(preferences_row.locations_json, [])
                if isinstance(item, str) and item.strip()
            ]

            for action in actions:
                edge = edges_by_id[action.edge_id]
                node = nodes_by_id.get(edge.node_id)
                if node is None or node.node_type not in {"skill", "location"}:
                    raise ValueError("Only inferred skill/location preferences are confirmable")

                decision_value = action.decision.value
                detail: dict[str, Any] = {}

                if action.decision == InferredPreferenceDecision.accept:
                    accepted_count += 1
                    if node.node_type == "skill":
                        interests = self._add_manual_interest(interests, node.label)
                    else:
                        locations = self._add_manual_location(locations, node.label)
                elif action.decision == InferredPreferenceDecision.reject:
                    rejected_count += 1
                    if node.node_type == "skill":
                        interests = self._remove_manual_interest(interests, node.canonical_key)
                    else:
                        locations = self._remove_manual_location(locations, node.canonical_key)
                else:
                    edited_label = (action.edited_label or "").strip()
                    if not edited_label:
                        raise ValueError("editedLabel is required when decision is EDIT")
                    edited_count += 1
                    detail = {"edited_label": edited_label}
                    if node.node_type == "skill":
                        interests = self._remove_manual_interest(interests, node.canonical_key)
                        interests = self._add_manual_interest(interests, edited_label)
                    else:
                        locations = self._remove_manual_location(locations, node.canonical_key)
                        locations = self._add_manual_location(locations, edited_label)

                session.add(
                    PreferenceFeedbackRow(
                        id=str(uuid4()),
                        user_id=user_id,
                        profile_id=profile.id,
                        node_id=node.id,
                        edge_id=edge.id,
                        node_type=node.node_type,
                        canonical_key=node.canonical_key,
                        resume_sha256=resume_sha,
                        decision=decision_value,
                        feedback_source="manual",
                        detail_json=_json_dumps(detail),
                        created_at=now,
                    )
                )

            preferences_row.interests_json = _json_dumps(interests)
            preferences_row.locations_json = _json_dumps(locations)
            preferences_row.updated_at = now
            if not preferences_row.created_at:
                preferences_row.created_at = now

            self._synthesize_preference_profile(
                session=session,
                user_id=user_id,
                preferences_row=preferences_row,
                resume_row=resume_row,
                now=now,
                trigger_source="manual",
            )

            session.commit()

        inferred_preferences = self.list_inferred_preferences(
            user_id=user_id,
            status_filter=InferredPreferenceStatus.pending,
        )
        return ConfirmInferredPreferencesResponse(
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            edited_count=edited_count,
            remaining_pending_count=len(inferred_preferences),
            inferred_preferences=inferred_preferences,
        )

    def record_recommendation_impressions(
        self,
        *,
        user_id: str,
        run_id: str,
        matches: Sequence[MatchedJob],
        variant: str,
    ) -> None:
        if not matches:
            return
        now = utc_now()
        with self._session_factory() as session:
            existing_rows = session.scalars(
                select(RecommendationImpressionRow).where(
                    and_(
                        RecommendationImpressionRow.user_id == user_id,
                        RecommendationImpressionRow.run_id == run_id,
                    )
                )
            ).all()
            for row in existing_rows:
                session.delete(row)

            for index, match in enumerate(matches, start=1):
                reason_text = match.reason.lower() if isinstance(match.reason, str) else ""
                hard_constraint_violation = "hard constraint violation" in reason_text
                session.add(
                    RecommendationImpressionRow(
                        id=str(uuid4()),
                        user_id=user_id,
                        run_id=run_id,
                        external_job_id=match.external_job_id,
                        title=match.title,
                        position=index,
                        score=match.score,
                        variant=variant,
                        hard_constraint_violation=hard_constraint_violation,
                        displayed_at=now,
                    )
                )
            session.commit()

    def record_recommendation_event(
        self,
        *,
        user_id: str,
        event_type: str,
        external_job_id: str | None = None,
        run_id: str | None = None,
        application_id: str | None = None,
        detail: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        normalized_event_type = event_type.strip().lower()
        if not normalized_event_type:
            return
        with self._session_factory() as session:
            session.add(
                RecommendationEventRow(
                    id=str(uuid4()),
                    user_id=user_id,
                    run_id=run_id,
                    external_job_id=external_job_id,
                    application_id=application_id,
                    event_type=normalized_event_type,
                    detail_json=_json_dumps(detail or {}),
                    occurred_at=occurred_at or utc_now(),
                )
            )
            session.commit()

    def compute_user_evaluation_metrics(
        self,
        *,
        user_id: str,
        window_days: int,
        refresh: bool = False,
        gate_thresholds: dict[str, float] | None = None,
    ) -> EvaluationMetricsResponse:
        normalized_window_days = max(1, int(window_days))
        thresholds: dict[str, float] = dict(_DEFAULT_EVALUATION_GATE_THRESHOLDS)
        for key, value in (gate_thresholds or {}).items():
            try:
                thresholds[key] = float(value)
            except (TypeError, ValueError):
                continue

        with self._session_factory() as session:
            if not refresh:
                latest_snapshot = session.scalar(
                    select(EvaluationMetricSnapshotRow)
                    .where(
                        and_(
                            EvaluationMetricSnapshotRow.user_id == user_id,
                            EvaluationMetricSnapshotRow.window_days == normalized_window_days,
                        )
                    )
                    .order_by(EvaluationMetricSnapshotRow.computed_at.desc())
                    .limit(1)
                )
                if latest_snapshot is not None:
                    return self._snapshot_to_evaluation_metrics(latest_snapshot)

            window_start = utc_now() - timedelta(days=normalized_window_days)
            impressions = session.scalars(
                select(RecommendationImpressionRow).where(
                    and_(
                        RecommendationImpressionRow.user_id == user_id,
                        RecommendationImpressionRow.displayed_at >= window_start,
                    )
                )
            ).all()
            events = session.scalars(
                select(RecommendationEventRow).where(
                    and_(
                        RecommendationEventRow.user_id == user_id,
                        RecommendationEventRow.occurred_at >= window_start,
                    )
                )
            ).all()

            labels_by_impression_id = {impression.id: 0 for impression in impressions}
            clicks_by_impression_id = {impression.id: False for impression in impressions}
            submitted_by_impression_id = {impression.id: False for impression in impressions}

            impression_by_run_job = {
                (impression.run_id, impression.external_job_id): impression.id
                for impression in impressions
            }
            latest_impression_by_job: dict[str, RecommendationImpressionRow] = {}
            for impression in impressions:
                existing = latest_impression_by_job.get(impression.external_job_id)
                if existing is None or impression.displayed_at > existing.displayed_at:
                    latest_impression_by_job[impression.external_job_id] = impression

            for event in events:
                impression_id: str | None = None
                if event.run_id and event.external_job_id:
                    impression_id = impression_by_run_job.get((event.run_id, event.external_job_id))
                elif event.external_job_id:
                    latest_impression = latest_impression_by_job.get(event.external_job_id)
                    impression_id = latest_impression.id if latest_impression is not None else None
                if impression_id is None:
                    continue

                event_type = event.event_type.strip().lower()
                if event_type in _RECOMMENDATION_CLICK_EVENT_TYPES:
                    clicks_by_impression_id[impression_id] = True
                if event_type in _RECOMMENDATION_SUBMITTED_EVENT_TYPES:
                    submitted_by_impression_id[impression_id] = True

            for impression in impressions:
                impression_id = impression.id
                if submitted_by_impression_id[impression_id]:
                    labels_by_impression_id[impression_id] = 2
                elif clicks_by_impression_id[impression_id]:
                    labels_by_impression_id[impression_id] = 1

            impressions_count = len(impressions)
            clicks_count = sum(1 for clicked in clicks_by_impression_id.values() if clicked)
            applications_submitted_count = sum(
                1 for submitted in submitted_by_impression_id.values() if submitted
            )
            hard_constraint_violation_count = sum(
                1 for impression in impressions if impression.hard_constraint_violation
            )

            impressions_by_run: dict[str, list[RecommendationImpressionRow]] = {}
            for impression in impressions:
                impressions_by_run.setdefault(impression.run_id, []).append(impression)

            per_run_precision_5: list[float] = []
            per_run_precision_10: list[float] = []
            per_run_ndcg_10: list[float] = []
            for run_impressions in impressions_by_run.values():
                ranked = sorted(run_impressions, key=lambda item: item.position)
                labels = [labels_by_impression_id[item.id] for item in ranked]
                per_run_precision_5.append(self._precision_at_k(labels, k=5))
                per_run_precision_10.append(self._precision_at_k(labels, k=10))
                per_run_ndcg_10.append(self._ndcg_at_k(labels, k=10))

            precision_at_5 = (
                sum(per_run_precision_5) / len(per_run_precision_5)
                if per_run_precision_5
                else 0.0
            )
            precision_at_10 = (
                sum(per_run_precision_10) / len(per_run_precision_10)
                if per_run_precision_10
                else 0.0
            )
            ndcg_at_10 = (
                sum(per_run_ndcg_10) / len(per_run_ndcg_10)
                if per_run_ndcg_10
                else 0.0
            )
            ctr = (clicks_count / impressions_count) if impressions_count else 0.0
            apply_through_rate = (
                applications_submitted_count / impressions_count
                if impressions_count
                else 0.0
            )
            hard_constraint_violation_rate = (
                hard_constraint_violation_count / impressions_count
                if impressions_count
                else 0.0
            )

            gate_checks = self._build_gate_checks(
                precision_at_5=precision_at_5,
                precision_at_10=precision_at_10,
                ndcg_at_10=ndcg_at_10,
                hard_constraint_violation_rate=hard_constraint_violation_rate,
                ctr=ctr,
                apply_through_rate=apply_through_rate,
                thresholds=thresholds,
            )
            run_count = len(impressions_by_run)
            if (
                impressions_count < int(thresholds["min_impressions"])
                or run_count < int(thresholds["min_runs"])
            ):
                gate_status = EvaluationGateStatus.insufficient_data
            elif all(check.passed for check in gate_checks):
                gate_status = EvaluationGateStatus.passed
            else:
                gate_status = EvaluationGateStatus.failed

            computed_at = utc_now()
            snapshot = EvaluationMetricSnapshotRow(
                id=str(uuid4()),
                user_id=user_id,
                window_days=normalized_window_days,
                impressions=impressions_count,
                clicks=clicks_count,
                applications_submitted=applications_submitted_count,
                precision_at_5=precision_at_5,
                precision_at_10=precision_at_10,
                ndcg_at_10=ndcg_at_10,
                hard_constraint_violation_rate=hard_constraint_violation_rate,
                ctr=ctr,
                apply_through_rate=apply_through_rate,
                gate_status=gate_status.value,
                gate_checks_json=_json_dumps(
                    [check.model_dump(mode="json") for check in gate_checks]
                ),
                computed_at=computed_at,
            )
            session.add(snapshot)
            session.commit()

            return EvaluationMetricsResponse(
                window_days=normalized_window_days,
                impressions=impressions_count,
                clicks=clicks_count,
                applications_submitted=applications_submitted_count,
                precision_at_5=precision_at_5,
                precision_at_10=precision_at_10,
                ndcg_at_10=ndcg_at_10,
                hard_constraint_violation_rate=hard_constraint_violation_rate,
                ctr=ctr,
                apply_through_rate=apply_through_rate,
                gate_status=gate_status,
                gate_checks=gate_checks,
                computed_at=computed_at,
            )

    def score_matches_with_preference_graph(
        self,
        *,
        user_id: str,
        external_run_id: str,
        matches: Sequence[MatchedJob],
        apply_rerank: bool,
    ) -> List[MatchedJob]:
        if not matches:
            return []

        try:
            with self._session_factory() as session:
                profile = session.scalar(
                    select(PreferenceProfileRow)
                    .where(
                        and_(
                            PreferenceProfileRow.user_id == user_id,
                            PreferenceProfileRow.status == "active",
                        )
                    )
                    .order_by(PreferenceProfileRow.version.desc())
                    .limit(1)
                )
                if profile is None:
                    return list(matches)

                edge_rows = session.scalars(
                    select(PreferenceEdgeRow).where(PreferenceEdgeRow.profile_id == profile.id)
                ).all()
                if not edge_rows:
                    return list(matches)

                node_ids = [row.node_id for row in edge_rows]
                node_rows = session.scalars(
                    select(PreferenceNodeRow).where(PreferenceNodeRow.id.in_(node_ids))
                ).all()
                nodes_by_id = {row.id: row for row in node_rows}
                edges: list[GraphPreferenceEdge] = []
                for edge_row in edge_rows:
                    node = nodes_by_id.get(edge_row.node_id)
                    if node is None:
                        continue
                    edges.append(
                        GraphPreferenceEdge(
                            node_type=node.node_type,
                            canonical_key=node.canonical_key,
                            label=node.label,
                            source=edge_row.source,
                            confidence=edge_row.confidence,
                            weight=edge_row.weight,
                            hard_constraint=edge_row.hard_constraint,
                            relationship=edge_row.relationship,
                        )
                    )

                profile_vector = _json_loads(profile.semantic_vector_json, {})
                scored_matches: list[tuple[int, MatchedJob]] = []

                existing_explanations = session.scalars(
                    select(JobMatchExplanationRow).where(
                        and_(
                            JobMatchExplanationRow.user_id == user_id,
                            JobMatchExplanationRow.external_run_id == external_run_id,
                        )
                    )
                ).all()
                for existing_row in existing_explanations:
                    session.delete(existing_row)

                now = utc_now()
                for index, match in enumerate(matches):
                    breakdown = evaluate_match_with_graph(
                        title=match.title,
                        company=match.company,
                        location=match.location,
                        reason=match.reason,
                        base_score=match.score,
                        edges=edges,
                        profile_vector=profile_vector,
                    )
                    reasons: list[str] = []
                    if breakdown.matched_nodes:
                        reasons.append(
                            "matched_nodes="
                            + ",".join(list(breakdown.matched_nodes)[:4])
                        )
                    if breakdown.inferred_nodes:
                        reasons.append(
                            "inferred_nodes="
                            + ",".join(list(breakdown.inferred_nodes)[:3])
                        )
                    if breakdown.manual_override_nodes:
                        reasons.append(
                            "manual_overrides="
                            + ",".join(list(breakdown.manual_override_nodes)[:3])
                        )
                    if breakdown.constraint_reasons:
                        reasons.extend(list(breakdown.constraint_reasons)[:2])
                    if not reasons:
                        reasons.append("no_graph_signals")

                    session.add(
                        JobMatchExplanationRow(
                            id=str(uuid4()),
                            user_id=user_id,
                            external_run_id=external_run_id,
                            external_job_id=match.external_job_id,
                            graph_score=breakdown.graph_score,
                            semantic_score=breakdown.semantic_score,
                            final_score=breakdown.final_score,
                            explanations_json=_json_dumps(reasons),
                            created_at=now,
                        )
                    )

                    if breakdown.filtered_out and apply_rerank:
                        continue

                    if apply_rerank:
                        rerank_reason = (
                            f"{match.reason} [{_GRAPH_RE_RANK_REASON_PREFIX}: {'; '.join(reasons)}]"
                        )
                        updated = match.model_copy(
                            update={
                                "score": breakdown.final_score,
                                "reason": rerank_reason,
                            }
                        )
                    else:
                        updated = match
                    scored_matches.append((index, updated))

                session.commit()

                if not apply_rerank:
                    return [item for _, item in scored_matches]

                reranked = sorted(
                    scored_matches,
                    key=lambda item: (-item[1].score, item[0]),
                )
                return [item for _, item in reranked]
        except Exception:
            logger.exception(
                "preference_graph_match_scoring_failed",
                extra={"user_id": user_id, "external_run_id": external_run_id},
            )
            return list(matches)

    def record_preference_feedback(
        self,
        *,
        user_id: str,
        decision: str,
        detail: dict[str, Any] | None = None,
        profile_id: str | None = None,
        node_id: str | None = None,
        edge_id: str | None = None,
        feedback_source: str = "manual",
    ) -> None:
        with self._session_factory() as session:
            session.add(
                PreferenceFeedbackRow(
                    id=str(uuid4()),
                    user_id=user_id,
                    profile_id=profile_id,
                    node_id=node_id,
                    edge_id=edge_id,
                    decision=decision,
                    feedback_source=feedback_source,
                    detail_json=_json_dumps(detail or {}),
                    created_at=utc_now(),
                )
            )
            session.commit()

    def _active_preference_profile(
        self,
        *,
        session: Session,
        user_id: str,
    ) -> PreferenceProfileRow | None:
        return session.scalar(
            select(PreferenceProfileRow)
            .where(
                and_(
                    PreferenceProfileRow.user_id == user_id,
                    PreferenceProfileRow.status == "active",
                )
            )
            .order_by(PreferenceProfileRow.version.desc())
            .limit(1)
        )

    @staticmethod
    def _resume_fingerprint(resume_row: ResumeRow | None) -> str | None:
        if resume_row is None:
            return None
        if isinstance(resume_row.file_sha256, str):
            normalized_sha = resume_row.file_sha256.strip().lower()
            if normalized_sha:
                return normalized_sha
        resume_text = (resume_row.resume_text or "").strip()
        if not resume_text:
            return None
        return sha256_hex(resume_text.encode("utf-8"))

    def _latest_feedback_by_node_key(
        self,
        *,
        session: Session,
        user_id: str,
        resume_sha256: str | None,
    ) -> dict[tuple[str, str], PreferenceFeedbackRow]:
        stmt = (
            select(PreferenceFeedbackRow)
            .where(
                and_(
                    PreferenceFeedbackRow.user_id == user_id,
                    PreferenceFeedbackRow.node_type.is_not(None),
                    PreferenceFeedbackRow.canonical_key.is_not(None),
                )
            )
            .order_by(PreferenceFeedbackRow.created_at.desc())
        )
        if resume_sha256 is not None:
            stmt = stmt.where(PreferenceFeedbackRow.resume_sha256 == resume_sha256)

        rows = session.scalars(stmt).all()
        latest: dict[tuple[str, str], PreferenceFeedbackRow] = {}
        for row in rows:
            node_type = (row.node_type or "").strip().lower()
            canonical_key = (row.canonical_key or "").strip().lower()
            if not node_type or not canonical_key:
                continue
            key = (node_type, canonical_key)
            if key not in latest:
                latest[key] = row
        return latest

    @staticmethod
    def _status_from_feedback_decision(
        decision: str | None,
    ) -> InferredPreferenceStatus:
        normalized = (decision or "").strip().lower()
        if normalized == InferredPreferenceDecision.accept.value:
            return InferredPreferenceStatus.accepted
        if normalized == InferredPreferenceDecision.reject.value:
            return InferredPreferenceStatus.rejected
        if normalized == InferredPreferenceDecision.edit.value:
            return InferredPreferenceStatus.edited
        return InferredPreferenceStatus.pending

    @staticmethod
    def _canonicalize_interest_key(value: str) -> str:
        return normalize_interest_token(value)

    @classmethod
    def _canonicalize_location_key(cls, value: str) -> str:
        return cls._canonicalize_interest_key(value).replace("-", " ")

    @classmethod
    def _add_manual_interest(cls, current: Sequence[str], label: str) -> list[str]:
        normalized_label = label.strip()
        canonical_key = cls._canonicalize_interest_key(normalized_label)
        if not canonical_key:
            return [str(item).strip() for item in current if isinstance(item, str) and item.strip()]

        existing = [
            str(item).strip() for item in current if isinstance(item, str) and item.strip()
        ]
        existing_keys = {
            cls._canonicalize_interest_key(item)
            for item in existing
            if cls._canonicalize_interest_key(item)
        }
        if canonical_key in existing_keys:
            return existing
        return [*existing, normalized_label]

    @classmethod
    def _remove_manual_interest(cls, current: Sequence[str], key_or_label: str) -> list[str]:
        target_key = cls._canonicalize_interest_key(key_or_label.strip())
        if not target_key:
            return [str(item).strip() for item in current if isinstance(item, str) and item.strip()]
        remaining: list[str] = []
        for item in current:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned:
                continue
            if cls._canonicalize_interest_key(cleaned) == target_key:
                continue
            remaining.append(cleaned)
        return remaining

    @classmethod
    def _add_manual_location(cls, current: Sequence[str], label: str) -> list[str]:
        normalized_label = label.strip()
        canonical_key = cls._canonicalize_location_key(normalized_label)
        if not canonical_key:
            return [str(item).strip() for item in current if isinstance(item, str) and item.strip()]

        existing = [
            str(item).strip() for item in current if isinstance(item, str) and item.strip()
        ]
        existing_keys = {
            cls._canonicalize_location_key(item)
            for item in existing
            if cls._canonicalize_location_key(item)
        }
        if canonical_key in existing_keys:
            return existing
        return [*existing, normalized_label]

    @classmethod
    def _remove_manual_location(cls, current: Sequence[str], key_or_label: str) -> list[str]:
        target_key = cls._canonicalize_location_key(key_or_label.strip())
        if not target_key:
            return [str(item).strip() for item in current if isinstance(item, str) and item.strip()]
        remaining: list[str] = []
        for item in current:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned:
                continue
            if cls._canonicalize_location_key(cleaned) == target_key:
                continue
            remaining.append(cleaned)
        return remaining

    def _filter_rejected_resume_interests(
        self,
        *,
        session: Session,
        user_id: str,
        resume_sha256: str | None,
        parsed_interests: Sequence[str],
    ) -> list[str]:
        normalized_interests = [
            str(item).strip()
            for item in parsed_interests
            if isinstance(item, str) and item.strip()
        ]
        if not normalized_interests:
            return []
        if resume_sha256 is None:
            deduped: list[str] = []
            seen: set[str] = set()
            for interest in normalized_interests:
                canonical = self._canonicalize_interest_key(interest)
                if not canonical or canonical in seen:
                    continue
                seen.add(canonical)
                deduped.append(interest)
            return deduped

        latest_feedback_by_key = self._latest_feedback_by_node_key(
            session=session,
            user_id=user_id,
            resume_sha256=resume_sha256,
        )
        rejected_interest_keys = {
            canonical_key
            for (node_type, canonical_key), feedback in latest_feedback_by_key.items()
            if node_type == "skill"
            and feedback.decision == InferredPreferenceDecision.reject.value
        }

        filtered: list[str] = []
        seen: set[str] = set()
        for interest in normalized_interests:
            canonical = self._canonicalize_interest_key(interest)
            if not canonical or canonical in rejected_interest_keys or canonical in seen:
                continue
            seen.add(canonical)
            filtered.append(interest)
        return filtered

    @staticmethod
    def _precision_at_k(labels: Sequence[int], *, k: int) -> float:
        if k <= 0 or not labels:
            return 0.0
        denominator = min(k, len(labels))
        if denominator <= 0:
            return 0.0
        relevant = sum(1 for label in labels[:denominator] if label >= 1)
        return float(relevant) / float(denominator)

    @staticmethod
    def _ndcg_at_k(labels: Sequence[int], *, k: int) -> float:
        if k <= 0 or not labels:
            return 0.0
        limit = min(k, len(labels))
        if limit <= 0:
            return 0.0

        def _dcg(values: Sequence[int]) -> float:
            return sum(
                (float((2**value) - 1) / math.log2(index + 2.0))
                for index, value in enumerate(values)
            )

        top_labels = list(labels[:limit])
        dcg = _dcg(top_labels)
        ideal = sorted(labels, reverse=True)[:limit]
        ideal_dcg = _dcg(ideal)
        if ideal_dcg <= 0.0:
            return 0.0
        return dcg / ideal_dcg

    def _build_gate_checks(
        self,
        *,
        precision_at_5: float,
        precision_at_10: float,
        ndcg_at_10: float,
        hard_constraint_violation_rate: float,
        ctr: float,
        apply_through_rate: float,
        thresholds: dict[str, float],
    ) -> list[EvaluationGateCheck]:
        checks = [
            (
                "precision_at_5",
                precision_at_5,
                float(thresholds["precision_at_5_min"]),
                ">=",
                precision_at_5 >= float(thresholds["precision_at_5_min"]),
            ),
            (
                "precision_at_10",
                precision_at_10,
                float(thresholds["precision_at_10_min"]),
                ">=",
                precision_at_10 >= float(thresholds["precision_at_10_min"]),
            ),
            (
                "ndcg_at_10",
                ndcg_at_10,
                float(thresholds["ndcg_at_10_min"]),
                ">=",
                ndcg_at_10 >= float(thresholds["ndcg_at_10_min"]),
            ),
            (
                "hard_constraint_violation_rate",
                hard_constraint_violation_rate,
                float(thresholds["hard_constraint_violation_max"]),
                "<=",
                hard_constraint_violation_rate
                <= float(thresholds["hard_constraint_violation_max"]),
            ),
            (
                "ctr",
                ctr,
                float(thresholds["ctr_min"]),
                ">=",
                ctr >= float(thresholds["ctr_min"]),
            ),
            (
                "apply_through_rate",
                apply_through_rate,
                float(thresholds["apply_through_min"]),
                ">=",
                apply_through_rate >= float(thresholds["apply_through_min"]),
            ),
        ]
        return [
            EvaluationGateCheck(
                metric=metric,
                actual=actual,
                threshold=threshold,
                comparator=comparator,
                passed=passed,
            )
            for metric, actual, threshold, comparator, passed in checks
        ]

    def _snapshot_to_evaluation_metrics(
        self,
        snapshot: EvaluationMetricSnapshotRow,
    ) -> EvaluationMetricsResponse:
        raw_checks = _json_loads(snapshot.gate_checks_json, [])
        checks: list[EvaluationGateCheck] = []
        if isinstance(raw_checks, list):
            for item in raw_checks:
                if not isinstance(item, dict):
                    continue
                try:
                    checks.append(EvaluationGateCheck.model_validate(item))
                except Exception:
                    continue
        gate_status_raw = (snapshot.gate_status or "").strip().upper()
        if gate_status_raw == EvaluationGateStatus.passed.value:
            gate_status = EvaluationGateStatus.passed
        elif gate_status_raw == EvaluationGateStatus.failed.value:
            gate_status = EvaluationGateStatus.failed
        else:
            gate_status = EvaluationGateStatus.insufficient_data
        return EvaluationMetricsResponse(
            window_days=int(snapshot.window_days),
            impressions=int(snapshot.impressions),
            clicks=int(snapshot.clicks),
            applications_submitted=int(snapshot.applications_submitted),
            precision_at_5=float(snapshot.precision_at_5),
            precision_at_10=float(snapshot.precision_at_10),
            ndcg_at_10=float(snapshot.ndcg_at_10),
            hard_constraint_violation_rate=float(snapshot.hard_constraint_violation_rate),
            ctr=float(snapshot.ctr),
            apply_through_rate=float(snapshot.apply_through_rate),
            gate_status=gate_status,
            gate_checks=checks,
            computed_at=snapshot.computed_at,
        )

    def _synthesize_preference_profile(
        self,
        *,
        session: Session,
        user_id: str,
        preferences_row: UserPreferenceRow | None,
        resume_row: ResumeRow | None,
        now: datetime,
        trigger_source: str,
    ) -> None:
        manual_hypotheses = self._manual_preference_hypotheses(preferences_row)
        resume_hypotheses = self._resume_preference_hypotheses(
            session=session,
            user_id=user_id,
            resume_row=resume_row,
        )

        if not manual_hypotheses and not resume_hypotheses:
            return

        resume_keys = {
            (hypothesis.node_type, hypothesis.canonical_key)
            for hypothesis in resume_hypotheses
        }
        resolved_manual_hypotheses: list[PreferenceHypothesis] = []
        for hypothesis in manual_hypotheses:
            relationship = (
                "overrides"
                if (hypothesis.node_type, hypothesis.canonical_key) in resume_keys
                else hypothesis.relationship
            )
            resolved_manual_hypotheses.append(
                PreferenceHypothesis(
                    node_type=hypothesis.node_type,
                    canonical_key=hypothesis.canonical_key,
                    label=hypothesis.label,
                    source=hypothesis.source,
                    confidence=hypothesis.confidence,
                    weight=hypothesis.weight,
                    hard_constraint=hypothesis.hard_constraint,
                    relationship=relationship,
                    span_ref=hypothesis.span_ref,
                    rationale=hypothesis.rationale,
                    metadata=hypothesis.metadata,
                )
            )

        hypotheses = [*resolved_manual_hypotheses, *resume_hypotheses]
        if not hypotheses:
            return

        active_profiles = session.scalars(
            select(PreferenceProfileRow).where(
                and_(
                    PreferenceProfileRow.user_id == user_id,
                    PreferenceProfileRow.status == "active",
                )
            )
        ).all()
        for active in active_profiles:
            active.status = "superseded"
            active.updated_at = now

        next_version = session.scalar(
            select(func.max(PreferenceProfileRow.version)).where(
                PreferenceProfileRow.user_id == user_id
            )
        )
        profile = PreferenceProfileRow(
            id=str(uuid4()),
            user_id=user_id,
            version=int(next_version or 0) + 1,
            status="active",
            source=trigger_source,
            semantic_vector_json=_json_dumps(
                build_sparse_semantic_vector(
                    self._compose_profile_vector_text(
                        preferences_row=preferences_row,
                        resume_row=resume_row,
                        hypotheses=hypotheses,
                    )
                )
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()

        for hypothesis in hypotheses:
            node = self._upsert_preference_node(
                session=session,
                user_id=user_id,
                hypothesis=hypothesis,
                now=now,
            )
            edge = PreferenceEdgeRow(
                id=str(uuid4()),
                user_id=user_id,
                profile_id=profile.id,
                node_id=node.id,
                relationship=hypothesis.relationship,
                source=hypothesis.source,
                confidence=max(0.0, min(1.0, float(hypothesis.confidence))),
                weight=max(0.0, min(1.0, float(hypothesis.weight))),
                hard_constraint=bool(hypothesis.hard_constraint),
                priority=100 if hypothesis.source == "manual" else 10,
                valid_from=now,
                valid_to=None,
                version=profile.version,
                metadata_json=_json_dumps(hypothesis.metadata),
                created_at=now,
                updated_at=now,
            )
            session.add(edge)
            session.flush()
            if hypothesis.source == "resume_parse" and resume_row is not None:
                session.add(
                    PreferenceEvidenceRow(
                        id=str(uuid4()),
                        user_id=user_id,
                        resume_id=resume_row.id,
                        node_id=node.id,
                        source=hypothesis.source,
                        confidence=max(0.0, min(1.0, float(hypothesis.confidence))),
                        extractor_version=_PREFERENCE_GRAPH_EXTRACTOR_VERSION,
                        span_ref=hypothesis.span_ref,
                        rationale=hypothesis.rationale,
                        metadata_json=_json_dumps(hypothesis.metadata),
                        created_at=now,
                    )
                )

    def _compose_profile_vector_text(
        self,
        *,
        preferences_row: UserPreferenceRow | None,
        resume_row: ResumeRow | None,
        hypotheses: Sequence[PreferenceHypothesis],
    ) -> str:
        segments: list[str] = []
        if preferences_row is not None:
            segments.extend(
                [
                    str(item)
                    for item in _json_loads(preferences_row.interests_json, [])
                    if isinstance(item, str) and item.strip()
                ]
            )
            segments.extend(
                [
                    str(item)
                    for item in _json_loads(preferences_row.locations_json, [])
                    if isinstance(item, str) and item.strip()
                ]
            )
            if preferences_row.seniority:
                segments.append(preferences_row.seniority)
        if resume_row is not None and resume_row.resume_text:
            segments.append(resume_row.resume_text[:4000])
        segments.extend(hypothesis.label for hypothesis in hypotheses if hypothesis.label)
        return "\n".join(segments).strip()

    def _manual_preference_hypotheses(
        self,
        preferences_row: UserPreferenceRow | None,
    ) -> list[PreferenceHypothesis]:
        if preferences_row is None:
            return []

        hypotheses: list[PreferenceHypothesis] = []
        for interest in _json_loads(preferences_row.interests_json, []):
            if not isinstance(interest, str) or not interest.strip():
                continue
            canonical = normalize_interest_token(interest)
            if not canonical:
                continue
            hypotheses.append(
                PreferenceHypothesis(
                    node_type="skill",
                    canonical_key=canonical,
                    label=interest.strip(),
                    source="manual",
                    confidence=1.0,
                    weight=1.0,
                    hard_constraint=False,
                    relationship="prefers",
                    rationale="manual_interest_update",
                )
            )

        for location in _json_loads(preferences_row.locations_json, []):
            if not isinstance(location, str) or not location.strip():
                continue
            canonical = normalize_interest_token(location).replace("-", " ")
            if not canonical:
                continue
            hypotheses.append(
                PreferenceHypothesis(
                    node_type="location",
                    canonical_key=canonical.replace(" ", "-"),
                    label=location.strip(),
                    source="manual",
                    confidence=1.0,
                    weight=1.0,
                    hard_constraint=True,
                    relationship="prefers",
                    rationale="manual_location_filter",
                )
            )

        if preferences_row.seniority and preferences_row.seniority.strip():
            normalized_seniority = preferences_row.seniority.strip()
            hypotheses.append(
                PreferenceHypothesis(
                    node_type="role",
                    canonical_key=normalize_interest_token(normalized_seniority),
                    label=normalized_seniority,
                    source="manual",
                    confidence=1.0,
                    weight=0.8,
                    hard_constraint=False,
                    relationship="prefers",
                    rationale="manual_seniority_preference",
                )
            )

        dedupe: dict[tuple[str, str], PreferenceHypothesis] = {}
        for hypothesis in hypotheses:
            key = (hypothesis.node_type, hypothesis.canonical_key)
            if key not in dedupe:
                dedupe[key] = hypothesis
        return list(dedupe.values())

    def _resume_preference_hypotheses(
        self,
        *,
        session: Session,
        user_id: str,
        resume_row: ResumeRow | None,
    ) -> list[PreferenceHypothesis]:
        if resume_row is None or not resume_row.resume_text.strip():
            return []
        parsed_interests = extract_resume_interests(resume_row.resume_text)
        hypotheses = extract_resume_preference_hypotheses(
            resume_text=resume_row.resume_text,
            parsed_interests=parsed_interests,
        )
        if not hypotheses:
            return []

        resume_sha = self._resume_fingerprint(resume_row)
        if resume_sha is None:
            return hypotheses

        latest_feedback_by_key = self._latest_feedback_by_node_key(
            session=session,
            user_id=user_id,
            resume_sha256=resume_sha,
        )
        rejected_keys = {
            (node_type, canonical_key)
            for (node_type, canonical_key), feedback in latest_feedback_by_key.items()
            if feedback.decision == InferredPreferenceDecision.reject.value
        }
        filtered: list[PreferenceHypothesis] = []
        seen: set[tuple[str, str]] = set()
        for hypothesis in hypotheses:
            key = (hypothesis.node_type, hypothesis.canonical_key)
            if key in rejected_keys or key in seen:
                continue
            seen.add(key)
            filtered.append(hypothesis)
        return filtered

    def _upsert_preference_node(
        self,
        *,
        session: Session,
        user_id: str,
        hypothesis: PreferenceHypothesis,
        now: datetime,
    ) -> PreferenceNodeRow:
        row = session.scalar(
            select(PreferenceNodeRow).where(
                and_(
                    PreferenceNodeRow.user_id == user_id,
                    PreferenceNodeRow.node_type == hypothesis.node_type,
                    PreferenceNodeRow.canonical_key == hypothesis.canonical_key,
                )
            )
        )
        if row is None:
            row = PreferenceNodeRow(
                id=str(uuid4()),
                user_id=user_id,
                node_type=hypothesis.node_type,
                canonical_key=hypothesis.canonical_key,
                label=hypothesis.label,
                attributes_json=_json_dumps(hypothesis.metadata),
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return row

        row.label = hypothesis.label
        row.attributes_json = _json_dumps(hypothesis.metadata)
        row.updated_at = now
        return row

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
                ).order_by(JobMatchRow.score.desc(), JobMatchRow.created_at.asc())
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
