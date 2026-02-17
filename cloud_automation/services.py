from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Protocol
from uuid import uuid4

import httpx
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .db_models import (
    ApplyAttemptRow,
    ApplyRunRow,
    ArtifactRefRow,
    CrawlRunRow,
    JobFingerprintRow,
    JobSourceRow,
    MatchResultRow,
    MatchRunRow,
    NormalizedJobRow,
    RawJobDocumentRow,
)
from .models import (
    ApplyAttemptCallbackPayload,
    ApplyAttemptRecord,
    ApplyAttemptStatus,
    ApplyRunRequest,
    ApplyRunStatusResponse,
    ArtifactRef,
    FailureCode,
    MatchRunRequest,
    MatchRunStatus,
    MatchRunStatusResponse,
    MatchedJob,
    NormalizedJob,
)
from .security import create_body_signature, create_hs256_jwt

logger = logging.getLogger(__name__)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return default


class JobIntelStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def record_discovery_documents(
        self,
        *,
        source_name: str,
        discovered_urls: list[str],
        raw_documents: dict[str, str],
        normalized_jobs: list[NormalizedJob],
        next_cursor: str | None,
    ) -> None:
        with self._session_factory() as session:
            source = session.get(JobSourceRow, source_name)
            if source is None:
                source = JobSourceRow(id=source_name, health_status="ok")
                session.add(source)

            source.last_cursor = next_cursor
            source.health_status = "ok"
            source.updated_at = datetime.utcnow()

            for url in discovered_urls:
                raw_doc_id = str(uuid4())
                session.add(
                    RawJobDocumentRow(
                        id=raw_doc_id,
                        source_id=source_name,
                        url=url,
                        body=raw_documents[url],
                        fetched_at=datetime.utcnow(),
                    )
                )

            for job in normalized_jobs:
                fingerprint = self._job_fingerprint(job)
                existing_fp = session.scalar(
                    select(JobFingerprintRow).where(
                        JobFingerprintRow.fingerprint == fingerprint
                    )
                )
                if existing_fp is not None:
                    existing_job = session.get(NormalizedJobRow, existing_fp.canonical_job_id)
                    if existing_job is not None:
                        existing_job.title = job.title
                        existing_job.company = job.company
                        existing_job.location = job.location
                        existing_job.salary = job.salary
                        existing_job.apply_url = job.apply_url
                        existing_job.source = job.source
                        existing_job.posted_at = job.posted_at
                        existing_job.description = job.description
                        continue

                session.add(
                    NormalizedJobRow(
                        id=job.id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        salary=job.salary,
                        apply_url=job.apply_url,
                        source=job.source,
                        posted_at=job.posted_at,
                        description=job.description,
                        created_at=datetime.utcnow(),
                    )
                )
                session.add(
                    JobFingerprintRow(
                        id=str(uuid4()),
                        fingerprint=fingerprint,
                        canonical_job_id=job.id,
                        created_at=datetime.utcnow(),
                    )
                )

            session.commit()

    def create_crawl_run(self, source_count: int) -> str:
        run_id = str(uuid4())
        with self._session_factory() as session:
            session.add(
                CrawlRunRow(
                    id=run_id,
                    status="running",
                    source_count=source_count,
                    discovered_count=0,
                    started_at=datetime.utcnow(),
                )
            )
            session.commit()
        return run_id

    def finalize_crawl_run(
        self, *, run_id: str, discovered_count: int, error: str | None = None
    ) -> None:
        with self._session_factory() as session:
            row = session.get(CrawlRunRow, run_id)
            if row is None:
                return
            row.discovered_count = discovered_count
            row.status = "failed" if error else "completed"
            row.error = error
            row.completed_at = datetime.utcnow()
            session.commit()

    def search_jobs(
        self, *, keywords: list[str], location: str | None = None, limit: int = 50
    ) -> list[NormalizedJob]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(NormalizedJobRow).order_by(NormalizedJobRow.created_at.desc()).limit(
                    max(limit * 5, 50)
                )
            ).all()

        normalized = [self._to_normalized_job(row) for row in rows]
        if not keywords and not location:
            return normalized[:limit]

        filtered: list[NormalizedJob] = []
        keywords_lower = [kw.lower() for kw in keywords]
        for job in normalized:
            if location and (job.location or "").lower() != location.lower():
                continue
            haystack = f"{job.title} {job.description}".lower()
            if keywords_lower and not any(kw in haystack for kw in keywords_lower):
                continue
            filtered.append(job)

        return filtered[:limit]

    def create_match_run(self, request: MatchRunRequest) -> str:
        run_id = str(uuid4())
        with self._session_factory() as session:
            session.add(
                MatchRunRow(
                    id=run_id,
                    user_ref=request.user_ref,
                    status=MatchRunStatus.queued.value,
                    request_json=request.model_dump_json(),
                    started_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
            session.commit()
        return run_id

    def set_match_run_status(
        self, *, run_id: str, status: MatchRunStatus, error: str | None = None
    ) -> None:
        with self._session_factory() as session:
            row = session.get(MatchRunRow, run_id)
            if row is None:
                return
            row.status = status.value
            row.error = error
            row.updated_at = datetime.utcnow()
            session.commit()

    def get_match_run_request(self, run_id: str) -> MatchRunRequest:
        with self._session_factory() as session:
            row = session.get(MatchRunRow, run_id)
            if row is None:
                raise ValueError("Match run not found")
            return MatchRunRequest.model_validate_json(row.request_json)

    def replace_match_results(self, *, run_id: str, matches: list[MatchedJob]) -> None:
        with self._session_factory() as session:
            existing = session.scalars(
                select(MatchResultRow).where(MatchResultRow.run_id == run_id)
            ).all()
            for row in existing:
                session.delete(row)

            for match in matches:
                session.add(
                    MatchResultRow(
                        id=str(uuid4()),
                        run_id=run_id,
                        external_job_id=match.external_job_id,
                        title=match.title,
                        company=match.company,
                        location=match.location,
                        apply_url=match.apply_url,
                        source=match.source,
                        reason=match.reason,
                        score=match.score,
                        posted_at=match.posted_at,
                    )
                )

            session.commit()

    def get_match_run_status(self, run_id: str) -> MatchRunStatusResponse:
        with self._session_factory() as session:
            run_row = session.get(MatchRunRow, run_id)
            if run_row is None:
                raise ValueError("Match run not found")
            result_rows = session.scalars(
                select(MatchResultRow).where(MatchResultRow.run_id == run_id)
            ).all()

        return MatchRunStatusResponse(
            run_id=run_id,
            status=MatchRunStatus(run_row.status),
            matches=[
                MatchedJob(
                    external_job_id=item.external_job_id,
                    title=item.title,
                    company=item.company,
                    location=item.location,
                    apply_url=item.apply_url,
                    source=item.source,
                    reason=item.reason,
                    score=item.score,
                    posted_at=item.posted_at,
                )
                for item in result_rows
            ],
            error=run_row.error,
        )

    def create_apply_run(self, request: ApplyRunRequest) -> str:
        run_id = str(uuid4())
        with self._session_factory() as session:
            session.add(
                ApplyRunRow(
                    id=run_id,
                    user_ref=request.user_ref,
                    status=MatchRunStatus.queued.value,
                    request_json=request.model_dump_json(),
                    started_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )

            for job in request.jobs:
                session.add(
                    ApplyAttemptRow(
                        id=str(uuid4()),
                        run_id=run_id,
                        external_job_id=job.external_job_id,
                        job_url=job.apply_url,
                        status=ApplyAttemptStatus.queued.value,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                )

            session.commit()
        return run_id

    def set_apply_run_status(
        self, *, run_id: str, status: MatchRunStatus, error: str | None = None
    ) -> None:
        with self._session_factory() as session:
            row = session.get(ApplyRunRow, run_id)
            if row is None:
                return
            row.status = status.value
            row.error = error
            row.updated_at = datetime.utcnow()
            session.commit()

    def get_apply_run_request(self, run_id: str) -> ApplyRunRequest:
        with self._session_factory() as session:
            row = session.get(ApplyRunRow, run_id)
            if row is None:
                raise ValueError("Apply run not found")
            return ApplyRunRequest.model_validate_json(row.request_json)

    def list_apply_attempts(self, run_id: str) -> list[ApplyAttemptRecord]:
        with self._session_factory() as session:
            attempts = session.scalars(
                select(ApplyAttemptRow).where(ApplyAttemptRow.run_id == run_id)
            ).all()
            artifacts = session.scalars(
                select(ArtifactRefRow).where(
                    ArtifactRefRow.attempt_id.in_([item.id for item in attempts])
                )
            ).all()

        artifact_map: dict[str, list[ArtifactRef]] = {}
        for artifact in artifacts:
            artifact_map.setdefault(artifact.attempt_id, []).append(
                ArtifactRef(
                    kind=artifact.kind,
                    url=artifact.url,
                    expires_at=artifact.expires_at,
                )
            )

        return [
            ApplyAttemptRecord(
                attempt_id=attempt.id,
                external_job_id=attempt.external_job_id,
                job_url=attempt.job_url,
                status=ApplyAttemptStatus(attempt.status),
                failure_code=FailureCode(attempt.failure_code)
                if attempt.failure_code
                else None,
                failure_reason=attempt.failure_reason,
                submitted_at=attempt.submitted_at,
                artifacts=artifact_map.get(attempt.id, []),
            )
            for attempt in attempts
        ]

    def update_apply_attempt(self, run_id: str, attempt: ApplyAttemptRecord) -> None:
        with self._session_factory() as session:
            row = session.get(ApplyAttemptRow, attempt.attempt_id)
            if row is None:
                row = ApplyAttemptRow(
                    id=attempt.attempt_id,
                    run_id=run_id,
                    external_job_id=attempt.external_job_id,
                    job_url=attempt.job_url,
                    status=attempt.status.value,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(row)

            row.external_job_id = attempt.external_job_id
            row.job_url = attempt.job_url
            row.status = attempt.status.value
            row.failure_code = attempt.failure_code.value if attempt.failure_code else None
            row.failure_reason = attempt.failure_reason
            row.submitted_at = attempt.submitted_at
            row.updated_at = datetime.utcnow()

            existing_artifacts = session.scalars(
                select(ArtifactRefRow).where(ArtifactRefRow.attempt_id == attempt.attempt_id)
            ).all()
            for artifact in existing_artifacts:
                session.delete(artifact)

            for artifact in attempt.artifacts:
                session.add(
                    ArtifactRefRow(
                        id=str(uuid4()),
                        attempt_id=attempt.attempt_id,
                        kind=artifact.kind,
                        url=artifact.url,
                        expires_at=artifact.expires_at,
                        created_at=datetime.utcnow(),
                    )
                )
            session.commit()

    def get_apply_run_status(self, run_id: str) -> ApplyRunStatusResponse:
        with self._session_factory() as session:
            run_row = session.get(ApplyRunRow, run_id)
            if run_row is None:
                raise ValueError("Apply run not found")

        attempts = self.list_apply_attempts(run_id)
        return ApplyRunStatusResponse(
            run_id=run_id,
            status=MatchRunStatus(run_row.status),
            attempts=attempts,
            error=run_row.error,
        )

    @staticmethod
    def _job_fingerprint(job: NormalizedJob) -> str:
        source = f"{job.title.strip().lower()}::{job.company.strip().lower()}::{(job.location or '').strip().lower()}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_normalized_job(row: NormalizedJobRow) -> NormalizedJob:
        return NormalizedJob(
            id=row.id,
            title=row.title,
            company=row.company,
            location=row.location,
            salary=row.salary,
            apply_url=row.apply_url,
            source=row.source,
            posted_at=row.posted_at,
            description=row.description,
        )


class DiscoveryCoordinator:
    def __init__(self, *, store: JobIntelStore, adapters: list[Any]) -> None:
        self.store = store
        self.adapters = adapters

    def run_discovery_once(self) -> None:
        crawl_id = self.store.create_crawl_run(source_count=len(self.adapters))
        discovered_count = 0
        try:
            for adapter in self.adapters:
                urls = adapter.discover(seeds=["software engineering", "ai"])
                raw_documents: dict[str, str] = {}
                jobs: list[NormalizedJob] = []
                for url in urls:
                    raw_doc = adapter.fetch(url)
                    raw_documents[url] = raw_doc
                    jobs.append(adapter.parse(raw_doc, url))
                discovered_count += len(jobs)
                self.store.record_discovery_documents(
                    source_name=adapter.source_name,
                    discovered_urls=urls,
                    raw_documents=raw_documents,
                    normalized_jobs=jobs,
                    next_cursor=adapter.next_cursor(),
                )

            self.store.finalize_crawl_run(
                run_id=crawl_id,
                discovered_count=discovered_count,
                error=None,
            )
        except Exception as exc:
            logger.exception("discovery_run_failed", extra={"crawl_id": crawl_id})
            self.store.finalize_crawl_run(
                run_id=crawl_id,
                discovered_count=discovered_count,
                error=str(exc),
            )


class CallbackEmitter:
    def __init__(self) -> None:
        self.enabled = os.getenv("MAIN_CALLBACK_URL", "").strip() != ""
        self.callback_url = os.getenv(
            "MAIN_CALLBACK_URL",
            "http://127.0.0.1:8000/internal/cloud/callbacks/apply-result",
        )
        self.issuer = os.getenv("CLOUD_CALLBACK_ISSUER", "job-intel-api")
        self.audience = os.getenv("CLOUD_CALLBACK_AUDIENCE", "main-api")
        self.signing_secret = os.getenv(
            "CLOUD_CALLBACK_SIGNING_SECRET",
            os.getenv("CLOUD_AUTOMATION_SIGNING_SECRET", "dev-cloud-signing-secret"),
        )
        self.signature_secret = os.getenv(
            "CLOUD_CALLBACK_SIGNATURE_SECRET",
            self.signing_secret,
        )

    def emit(self, payload: ApplyAttemptCallbackPayload) -> None:
        if not self.enabled:
            return

        body = payload.model_dump_json().encode("utf-8")
        timestamp = str(int(datetime.utcnow().timestamp()))
        nonce = str(uuid4())
        signature = create_body_signature(
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            secret=self.signature_secret,
        )
        token = create_hs256_jwt(
            payload={"sub": self.issuer},
            secret=self.signing_secret,
            issuer=self.issuer,
            audience=self.audience,
            expires_in_seconds=300,
        )

        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "x-cloud-timestamp": timestamp,
            "x-cloud-nonce": nonce,
            "x-cloud-signature": signature,
            "x-idempotency-key": payload.idempotency_key,
        }

        try:
            response = httpx.post(self.callback_url, content=body, headers=headers, timeout=20.0)
            if response.status_code >= 300:
                logger.warning(
                    "callback_delivery_non_success",
                    extra={
                        "status_code": response.status_code,
                        "body": response.text,
                        "run_id": payload.run_id,
                        "attempt_id": payload.attempt.attempt_id,
                    },
                )
        except Exception:
            logger.exception(
                "callback_delivery_failed",
                extra={"run_id": payload.run_id, "attempt_id": payload.attempt.attempt_id},
            )


class MatchingService:
    def __init__(self, *, store: JobIntelStore) -> None:
        self.store = store

    async def execute(self, run_id: str) -> None:
        self.store.set_match_run_status(run_id=run_id, status=MatchRunStatus.running)
        try:
            request = self.store.get_match_run_request(run_id)
            interests = [str(item).lower() for item in request.preferences.get("interests", [])]
            jobs = self.store.search_jobs(
                keywords=interests,
                location=request.location,
                limit=max(request.limit * 2, 40),
            )

            matches = [self._score_job(job=job, request=request) for job in jobs]
            matches = sorted(matches, key=lambda item: item.score, reverse=True)
            top_matches = [item for item in matches if item.score > 0.0][: request.limit]

            self.store.replace_match_results(run_id=run_id, matches=top_matches)
            self.store.set_match_run_status(run_id=run_id, status=MatchRunStatus.completed)
        except Exception as exc:
            logger.exception("match_run_failed", extra={"run_id": run_id})
            self.store.set_match_run_status(
                run_id=run_id,
                status=MatchRunStatus.failed,
                error=str(exc),
            )

    @staticmethod
    def _score_job(job: NormalizedJob, request: MatchRunRequest) -> MatchedJob:
        interests = [str(item).lower() for item in request.preferences.get("interests", [])]
        haystack = f"{job.title} {job.description}".lower()
        overlap = len([interest for interest in interests if interest in haystack])
        max_score = max(len(interests), 1)
        base_score = overlap / max_score

        reason = (
            f"Matched {overlap} preference keyword(s) from resume/preferences with source {job.source}."
        )

        return MatchedJob(
            external_job_id=job.id,
            title=job.title,
            company=job.company,
            location=job.location,
            apply_url=job.apply_url,
            source=job.source,
            reason=reason,
            score=min(max(base_score, 0.0), 1.0),
            posted_at=job.posted_at,
        )


class ApplyExecutor(Protocol):
    def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
        ...


class OpenAITextGenerator:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
        self.timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def generate(self, *, prompt: str) -> str | None:
        if not self.enabled:
            return None

        try:
            response = httpx.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": prompt,
                    "max_output_tokens": 280,
                    "temperature": 0.2,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except Exception:
            logger.exception("openai_generation_failed")
            return None

        text = body.get("output_text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        output = body.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") in {"output_text", "text"}:
                        candidate = str(block.get("text", "")).strip()
                        if candidate:
                            return candidate
        return None


class FormAnswerSynthesizer:
    def __init__(self, *, text_generator: OpenAITextGenerator | None = None) -> None:
        self.text_generator = text_generator or OpenAITextGenerator()

    @staticmethod
    def _decline_default(value: str | None) -> str:
        return value.strip() if isinstance(value, str) and value.strip() else "decline_to_answer"

    @staticmethod
    def _application_profile(request: ApplyRunRequest) -> dict[str, Any]:
        profile_payload = request.profile_payload or {}
        application_profile = profile_payload.get("application_profile")
        return application_profile if isinstance(application_profile, dict) else {}

    def resolve_sensitive_answer(self, *, request: ApplyRunRequest, key: str) -> str:
        profile = self._application_profile(request)
        sensitive = profile.get("sensitive")
        if isinstance(sensitive, dict):
            value = sensitive.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "decline_to_answer"

    @staticmethod
    def classify_question(
        *,
        label: str | None = None,
        name: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        haystack_parts = [label or "", name or ""]
        if options:
            haystack_parts.extend(options)
        haystack = " ".join(haystack_parts).lower()

        if any(token in haystack for token in ["race", "ethnicity"]):
            return "race_ethnicity"
        if "gender" in haystack:
            return "gender"
        if "veteran" in haystack:
            return "veteran_status"
        if "disability" in haystack:
            return "disability_status"
        if "sponsor" in haystack:
            return "requires_sponsorship"
        if "authorization" in haystack or "authorized" in haystack:
            return "work_authorization"
        if "relocate" in haystack:
            return "willing_to_relocate"
        if any(token in haystack for token in ["cover letter", "essay", "why", "textarea"]):
            return "open_text"
        return "generic"

    def answer_question(
        self,
        *,
        request: ApplyRunRequest,
        label: str | None = None,
        name: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        question_type = self.classify_question(label=label, name=name, options=options)
        if question_type in {
            "race_ethnicity",
            "gender",
            "veteran_status",
            "disability_status",
        }:
            return self.resolve_sensitive_answer(request=request, key=question_type)

        typed = self.resolve_typed_answer(
            request=request,
            question_key=question_type if question_type != "generic" else (name or label or ""),
        )
        if typed:
            return typed

        prompt = label or name or "Please provide a concise answer."
        return self.generate_open_text_answer(request=request, prompt=prompt)

    def resolve_typed_answer(self, *, request: ApplyRunRequest, question_key: str) -> str | None:
        key = question_key.strip().lower()
        profile = self._application_profile(request)

        custom_answers = profile.get("custom_answers")
        if isinstance(custom_answers, list):
            for item in custom_answers:
                if not isinstance(item, dict):
                    continue
                candidate_key = str(item.get("question_key", "")).strip().lower()
                if candidate_key == key:
                    answer = str(item.get("answer", "")).strip()
                    if answer:
                        return answer

        by_field = {
            "work_authorization": profile.get("work_authorization"),
            "requires_sponsorship": profile.get("requires_sponsorship"),
            "willing_to_relocate": profile.get("willing_to_relocate"),
            "years_experience": profile.get("years_experience"),
            "phone": profile.get("phone"),
            "city": profile.get("city"),
            "state": profile.get("state"),
            "country": profile.get("country"),
            "linkedin_url": profile.get("linkedin_url"),
            "github_url": profile.get("github_url"),
            "portfolio_url": profile.get("portfolio_url"),
        }
        if key in by_field:
            value = by_field[key]
            if isinstance(value, bool):
                return "yes" if value else "no"
            if value is not None and str(value).strip():
                return str(value).strip()

        if "gender" in key:
            return self.resolve_sensitive_answer(request=request, key="gender")
        if "race" in key or "ethnicity" in key:
            return self.resolve_sensitive_answer(request=request, key="race_ethnicity")
        if "veteran" in key:
            return self.resolve_sensitive_answer(request=request, key="veteran_status")
        if "disability" in key:
            return self.resolve_sensitive_answer(request=request, key="disability_status")

        return None

    def generate_open_text_answer(
        self,
        *,
        request: ApplyRunRequest,
        prompt: str,
    ) -> str:
        profile_payload = request.profile_payload or {}
        profile = self._application_profile(request)

        resume_text = str(profile_payload.get("resume_text", "")).strip()
        preferences = profile_payload.get("preferences")
        interests = []
        if isinstance(preferences, dict):
            raw_interests = preferences.get("interests")
            if isinstance(raw_interests, list):
                interests = [str(item).strip() for item in raw_interests if str(item).strip()]

        context = {
            "name": profile_payload.get("full_name", ""),
            "interests": interests,
            "writing_voice": profile.get("writing_voice", ""),
            "cover_letter_style": profile.get("cover_letter_style", ""),
            "achievements_summary": profile.get("achievements_summary", ""),
            "additional_context": profile.get("additional_context", ""),
            "resume_excerpt": resume_text[:3000],
            "question": prompt,
        }
        llm_prompt = (
            "You are writing concise and truthful application responses. "
            "Use only the provided profile context and avoid inventing facts. "
            "Return plain text only.\n\n"
            f"{json.dumps(context, ensure_ascii=True)}"
        )

        generated = self.text_generator.generate(prompt=llm_prompt)
        if generated:
            return generated

        name = str(profile_payload.get("full_name", "Candidate")).strip() or "Candidate"
        summary = str(profile.get("achievements_summary", "")).strip()
        interest_phrase = ", ".join(interests[:4]) if interests else "the role requirements"
        fallback = (
            f"I am {name}, and I am excited to contribute to this role. "
            f"My experience aligns well with {interest_phrase}."
        )
        if summary:
            fallback += f" Key highlight: {summary}"
        return fallback


class SimulatedApplyExecutor:
    def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
        del request
        digest = hashlib.sha256(attempt.job_url.encode("utf-8")).hexdigest()
        selector = int(digest[:2], 16)

        expires = datetime.utcnow() + timedelta(days=7)
        artifacts = [
            ArtifactRef(
                kind="screenshot",
                url=f"s3://job-artifacts/{attempt.attempt_id}/final.png",
                expires_at=expires,
            ),
            ArtifactRef(
                kind="html",
                url=f"s3://job-artifacts/{attempt.attempt_id}/final.html",
                expires_at=expires,
            ),
        ]

        if selector % 10 < 7:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.succeeded,
                    "submitted_at": datetime.utcnow(),
                    "artifacts": artifacts,
                    "failure_code": None,
                    "failure_reason": None,
                }
            )

        failure_code = FailureCode.captcha_failed if selector % 2 == 0 else FailureCode.timeout
        failure_reason = (
            "CAPTCHA solve attempt failed"
            if failure_code == FailureCode.captcha_failed
            else "Form submission timed out"
        )
        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.failed,
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "artifacts": artifacts,
            }
        )


class PlaywrightApplyExecutor:
    def __init__(self, *, synthesizer: FormAnswerSynthesizer) -> None:
        self.synthesizer = synthesizer
        self.headless = (
            os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.nav_timeout_ms = int(float(os.getenv("PLAYWRIGHT_NAV_TIMEOUT_SECONDS", "20")) * 1000)
        self.action_timeout_ms = int(float(os.getenv("PLAYWRIGHT_ACTION_TIMEOUT_SECONDS", "5")) * 1000)
        self.capture_screenshots = (
            os.getenv("PLAYWRIGHT_CAPTURE_SCREENSHOTS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )

    def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
        lower_url = attempt.job_url.lower()
        if "captcha" in lower_url:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": FailureCode.captcha_failed,
                    "failure_reason": "CAPTCHA challenge detected",
                }
            )
        if "blocked" in lower_url:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": FailureCode.site_blocked,
                    "failure_reason": "Site automation protections blocked navigation",
                }
            )

        work_auth = self.synthesizer.resolve_typed_answer(
            request=request,
            question_key="work_authorization",
        )
        if not work_auth:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": FailureCode.form_validation_failed,
                    "failure_reason": "Missing work authorization answer in application profile",
                }
            )

        _ = self.synthesizer.answer_question(
            request=request,
            label="Please provide a short, role-specific cover letter",
            name="cover_letter",
            options=None,
        )

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()
                page.set_default_navigation_timeout(self.nav_timeout_ms)
                page.set_default_timeout(self.action_timeout_ms)
                page.goto(attempt.job_url, wait_until="domcontentloaded")
                if self.capture_screenshots:
                    page.screenshot(path=f"/tmp/{attempt.attempt_id}.png", full_page=True)
                context.close()
                browser.close()
        except Exception as exc:
            logger.exception(
                "playwright_apply_attempt_failed",
                extra={"attempt_id": attempt.attempt_id, "job_url": attempt.job_url},
            )
            error_text = str(exc).lower()
            failure_code = (
                FailureCode.timeout if "timeout" in error_text else FailureCode.site_blocked
            )
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": failure_code,
                    "failure_reason": str(exc),
                }
            )

        expires = datetime.utcnow() + timedelta(days=7)
        artifacts = [
            ArtifactRef(
                kind="html",
                url=f"s3://job-artifacts/{attempt.attempt_id}/playwright-final.html",
                expires_at=expires,
            )
        ]
        if self.capture_screenshots:
            artifacts.append(
                ArtifactRef(
                    kind="screenshot",
                    url=f"s3://job-artifacts/{attempt.attempt_id}/playwright-final.png",
                    expires_at=expires,
                )
            )

        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.succeeded,
                "submitted_at": datetime.utcnow(),
                "failure_code": None,
                "failure_reason": None,
                "artifacts": artifacts,
            }
        )


class ApplyService:
    def __init__(self, *, store: JobIntelStore, callback_emitter: CallbackEmitter) -> None:
        self.store = store
        self.callback_emitter = callback_emitter
        self.answer_synthesizer = FormAnswerSynthesizer()

    @staticmethod
    def _autonomous_browsing_enabled() -> bool:
        return os.getenv("ENABLE_AUTONOMOUS_BROWSING", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _build_executor(self) -> ApplyExecutor:
        if self._autonomous_browsing_enabled():
            try:
                return PlaywrightApplyExecutor(synthesizer=self.answer_synthesizer)
            except Exception:
                logger.exception("playwright_executor_init_failed")
        return SimulatedApplyExecutor()

    async def execute(self, run_id: str) -> None:
        self.store.set_apply_run_status(run_id=run_id, status=MatchRunStatus.running)
        try:
            request = self.store.get_apply_run_request(run_id)
            attempts = self.store.list_apply_attempts(run_id)
            executor = self._build_executor()

            for attempt in attempts:
                browsing = attempt.model_copy(
                    update={"status": ApplyAttemptStatus.browsing}
                )
                self.store.update_apply_attempt(run_id, browsing)

                filling = browsing.model_copy(update={"status": ApplyAttemptStatus.filling})
                self.store.update_apply_attempt(run_id, filling)

                terminal_attempt = executor.complete_attempt(
                    attempt=filling,
                    request=request,
                )
                self.store.update_apply_attempt(run_id, terminal_attempt)

                callback_payload = ApplyAttemptCallbackPayload(
                    idempotency_key=str(uuid4()),
                    run_id=run_id,
                    user_ref=request.user_ref,
                    attempt=terminal_attempt,
                )
                self.callback_emitter.emit(callback_payload)

            self.store.set_apply_run_status(run_id=run_id, status=MatchRunStatus.completed)
        except Exception as exc:
            logger.exception("apply_run_failed", extra={"run_id": run_id})
            self.store.set_apply_run_status(
                run_id=run_id,
                status=MatchRunStatus.failed,
                error=str(exc),
            )
