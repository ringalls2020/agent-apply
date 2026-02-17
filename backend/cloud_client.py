from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict

import httpx

from .models import (
    CloudApplyRunCreated,
    CloudApplyRunRequest,
    CloudApplyRunStatus,
    CloudMatchRunCreated,
    CloudMatchRunRequest,
    CloudMatchRunStatus,
)
from .security import create_hs256_jwt


class CloudClientError(RuntimeError):
    pass


@dataclass
class CloudClientSettings:
    base_url: str
    service_id: str
    audience: str
    signing_secret: str
    timeout_seconds: float = 20.0


class CloudAutomationClient:
    def __init__(self, settings: CloudClientSettings) -> None:
        self._settings = settings

    @classmethod
    def from_env(cls) -> "CloudAutomationClient":
        settings = CloudClientSettings(
            base_url=os.getenv("CLOUD_AUTOMATION_BASE_URL", "http://127.0.0.1:8100"),
            service_id=os.getenv("CLOUD_AUTOMATION_SERVICE_ID", "main-api"),
            audience=os.getenv("CLOUD_AUTOMATION_AUDIENCE", "job-intel-api"),
            signing_secret=os.getenv(
                "CLOUD_AUTOMATION_SIGNING_SECRET", "dev-cloud-signing-secret"
            ),
            timeout_seconds=float(os.getenv("CLOUD_AUTOMATION_TIMEOUT_SECONDS", "20")),
        )
        return cls(settings=settings)

    def _auth_headers(self) -> Dict[str, str]:
        token = create_hs256_jwt(
            payload={"sub": self._settings.service_id},
            secret=self._settings.signing_secret,
            issuer=self._settings.service_id,
            audience=self._settings.audience,
            expires_in_seconds=300,
        )
        return {
            "authorization": f"Bearer {token}",
            "x-service-id": self._settings.service_id,
            "content-type": "application/json",
        }

    def _request(
        self,
        *,
        method: str,
        path: str,
        json_body: Dict[str, Any] | None = None,
        params: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        url = f"{self._settings.base_url.rstrip('/')}{path}"
        with httpx.Client(timeout=self._settings.timeout_seconds) as client:
            response = client.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                headers=self._auth_headers(),
            )

        if response.status_code >= 400:
            raise CloudClientError(
                f"Cloud API request failed: status={response.status_code} body={response.text}"
            )

        return response.json()

    def start_match_run(self, payload: CloudMatchRunRequest) -> CloudMatchRunCreated:
        body = self._request(
            method="POST",
            path="/v1/match-runs",
            json_body=payload.model_dump(mode="json"),
        )
        return CloudMatchRunCreated.model_validate(body)

    def get_match_run(self, run_id: str) -> CloudMatchRunStatus:
        body = self._request(method="GET", path=f"/v1/match-runs/{run_id}")
        return CloudMatchRunStatus.model_validate(body)

    def start_apply_run(self, payload: CloudApplyRunRequest) -> CloudApplyRunCreated:
        body = self._request(
            method="POST",
            path="/v1/apply-runs",
            json_body=payload.model_dump(mode="json"),
        )
        return CloudApplyRunCreated.model_validate(body)

    def get_apply_run(self, run_id: str) -> CloudApplyRunStatus:
        body = self._request(method="GET", path=f"/v1/apply-runs/{run_id}")
        return CloudApplyRunStatus.model_validate(body)
