from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import timedelta
from typing import Protocol
from uuid import uuid4

import httpx

from common.time import utc_now

from ..models import (
    ApplyAttemptCallbackPayload,
    ApplyAttemptRecord,
    ApplyAttemptStatus,
    ApplyRunRequest,
    ArtifactRef,
    FailureCode,
    MatchRunStatus,
)
from .answers import FormAnswerSynthesizer, OpenAITextGenerator
from .callbacks import CallbackEmitter
from .job_store import JobIntelStore
from .playwright import PlaywrightApplyExecutor

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if min_value is not None:
        value = max(value, min_value)
    return value

class ApplyExecutor(Protocol):
    async def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
        ...



class SimulatedApplyExecutor:
    async def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
        del request
        digest = hashlib.sha256(attempt.job_url.encode("utf-8")).hexdigest()
        selector = int(digest[:2], 16)

        expires = utc_now() + timedelta(days=7)
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
                    "submitted_at": utc_now(),
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


class ApplyExecutionFlags:
    DEV_REVIEW_ALLOWED_ENVS = {"local", "dev", "development", "test"}

    def __init__(self) -> None:
        self.autonomous_browsing_enabled = _bool_env("ENABLE_AUTONOMOUS_BROWSING", False)
        self.dev_review_requested = _bool_env("ENABLE_APPLY_DEV_REVIEW_MODE", False)
        self.app_env = (
            os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower()
            or "development"
        )
        self.dev_review_env_allowed = self.app_env in self.DEV_REVIEW_ALLOWED_ENVS
        self.dev_review_enabled = self.dev_review_requested and self.dev_review_env_allowed
        self.submit_timeout_seconds = _int_env(
            "APPLY_DEV_REVIEW_SUBMIT_TIMEOUT_SECONDS",
            300,
            min_value=1,
        )
        self.poll_interval_ms = _int_env(
            "APPLY_DEV_REVIEW_POLL_INTERVAL_MS",
            500,
            min_value=50,
        )
        self.slow_mo_ms = _int_env(
            "APPLY_DEV_REVIEW_SLOW_MO_MS",
            120,
            min_value=0,
        )



class ApplyService:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        callback_emitter: CallbackEmitter,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.store = store
        self.callback_emitter = callback_emitter
        self._owns_http_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=20.0)
        self.answer_synthesizer = FormAnswerSynthesizer(
            text_generator=OpenAITextGenerator(client=self.http_client)
        )

    def _build_executor(self) -> ApplyExecutor:
        flags = ApplyExecutionFlags()
        if flags.dev_review_requested and not flags.dev_review_env_allowed:
            logger.info(
                "apply_dev_review_mode_ignored_for_non_dev_environment",
                extra={"app_env": flags.app_env},
            )

        if flags.autonomous_browsing_enabled:
            try:
                return PlaywrightApplyExecutor(
                    synthesizer=self.answer_synthesizer,
                    dev_review_mode=flags.dev_review_enabled,
                    submit_timeout_seconds=flags.submit_timeout_seconds,
                    poll_interval_ms=flags.poll_interval_ms,
                    slow_mo_ms=flags.slow_mo_ms,
                )
            except Exception:
                logger.exception("playwright_executor_init_failed")
        return SimulatedApplyExecutor()

    def close(self) -> None:
        close_emitter = getattr(self.callback_emitter, "close", None)
        if callable(close_emitter):
            close_emitter()
        if self._owns_http_client:
            self.http_client.close()

    async def execute(self, run_id: str, *, assume_claimed: bool = False) -> None:
        if not assume_claimed and not self.store.claim_apply_run(run_id):
            logger.info("apply_run_not_claimed", extra={"run_id": run_id})
            return
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

                logger.info(
                    "apply_attempt_execution_started",
                    extra={
                        "run_id": run_id,
                        "attempt_id": filling.attempt_id,
                        "job_url": filling.job_url,
                        "executor_type": type(executor).__name__,
                    },
                )
                attempt_started_at = time.perf_counter()
                terminal_attempt = await executor.complete_attempt(
                    attempt=filling,
                    request=request,
                )
                attempt_duration_ms = round((time.perf_counter() - attempt_started_at) * 1000, 2)
                logger.info(
                    "apply_attempt_execution_completed",
                    extra={
                        "run_id": run_id,
                        "attempt_id": filling.attempt_id,
                        "job_url": filling.job_url,
                        "executor_type": type(executor).__name__,
                        "status": terminal_attempt.status.value,
                        "duration_ms": attempt_duration_ms,
                    },
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

__all__ = ["ApplyExecutor", "SimulatedApplyExecutor", "ApplyExecutionFlags", "ApplyService"]
