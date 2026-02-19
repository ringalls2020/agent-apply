from __future__ import annotations

import logging
import os
import time
from uuid import uuid4

import httpx

from common.time import utc_epoch_seconds

from ..models import ApplyAttemptCallbackPayload
from ..security import create_body_signature, create_hs256_jwt

logger = logging.getLogger(__name__)


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


class CallbackEmitter:
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        max_attempts: int | None = None,
        retry_base_delay_ms: int | None = None,
    ) -> None:
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
        self.max_attempts = max(
            max_attempts
            if max_attempts is not None
            else _int_env("CALLBACK_RETRY_MAX_ATTEMPTS", 3, min_value=1),
            1,
        )
        self.retry_base_delay_ms = max(
            retry_base_delay_ms
            if retry_base_delay_ms is not None
            else _int_env("CALLBACK_RETRY_BASE_DELAY_MS", 250, min_value=50),
            50,
        )
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=20.0)

    def emit(self, payload: ApplyAttemptCallbackPayload) -> None:
        if not self.enabled:
            return

        body = payload.model_dump_json().encode("utf-8")
        timestamp = str(utc_epoch_seconds())
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

        for attempt_index in range(self.max_attempts):
            try:
                response = self.http_client.post(
                    self.callback_url,
                    content=body,
                    headers=headers,
                    timeout=20.0,
                )
                if response.status_code < 300:
                    return
                logger.warning(
                    "callback_delivery_non_success",
                    extra={
                        "status_code": response.status_code,
                        "body": response.text,
                        "run_id": payload.run_id,
                        "attempt_id": payload.attempt.attempt_id,
                        "attempt_index": attempt_index + 1,
                        "max_attempts": self.max_attempts,
                    },
                )
            except Exception:
                logger.exception(
                    "callback_delivery_failed",
                    extra={
                        "run_id": payload.run_id,
                        "attempt_id": payload.attempt.attempt_id,
                        "attempt_index": attempt_index + 1,
                        "max_attempts": self.max_attempts,
                    },
                )

            if attempt_index + 1 < self.max_attempts:
                delay_seconds = (self.retry_base_delay_ms * (2**attempt_index)) / 1000.0
                time.sleep(delay_seconds)

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()


__all__ = ["CallbackEmitter"]
