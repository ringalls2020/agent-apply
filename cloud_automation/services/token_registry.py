from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from cloud_automation.adapters.live import (
    GreenhouseLiveAdapter,
    LeverLiveAdapter,
    SmartRecruitersLiveAdapter,
)

from .job_store import JobIntelStore

logger = logging.getLogger(__name__)


class TokenRegistryCoordinator:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        http_client: httpx.Client,
    ) -> None:
        self.store = store
        self.http_client = http_client
        self.timeout_seconds = float(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "20"))
        self.validation_recheck_hours = max(int(os.getenv("TOKEN_VALIDATION_RECHECK_HOURS", "24")), 1)
        self.validation_batch_size = max(int(os.getenv("TOKEN_VALIDATION_BATCH_SIZE", "2000")), 1)

    def validate_tokens_once(self) -> dict[str, int]:
        stats = {"validated": 0, "invalid": 0, "pending": 0}
        candidates = self.store.list_tokens_for_validation(
            recheck_hours=self.validation_recheck_hours,
            limit=self.validation_batch_size,
        )
        for token_row in candidates:
            status, error = self._validate_token(provider=token_row.provider, token=token_row.token)
            self.store.set_token_validation_result(
                provider=token_row.provider,
                token=token_row.token,
                status=status,
                error=error,
            )
            if status in stats:
                stats[status] += 1
        return stats

    def ingest_validated_jobs_once(self) -> int:
        tokens = self.store.list_validated_tokens_by_provider()
        adapters: list[Any] = []
        discovered_count = 0

        if tokens.get("greenhouse"):
            adapters.append(
                GreenhouseLiveAdapter(tokens["greenhouse"], timeout_seconds=self.timeout_seconds, client=self.http_client)
            )
        if tokens.get("lever"):
            adapters.append(
                LeverLiveAdapter(tokens["lever"], timeout_seconds=self.timeout_seconds, client=self.http_client)
            )
        if tokens.get("smartrecruiters"):
            adapters.append(
                SmartRecruitersLiveAdapter(
                    tokens["smartrecruiters"],
                    timeout_seconds=self.timeout_seconds,
                    client=self.http_client,
                )
            )

        for adapter in adapters:
            try:
                discovered_urls = adapter.discover(seeds=[], cursor=None)
            except Exception:
                logger.exception("validated_feed_discovery_failed", extra={"source": adapter.source_name})
                continue

            raw_documents: dict[str, str] = {}
            normalized_jobs = []
            for url in discovered_urls:
                try:
                    raw_doc = adapter.fetch(url)
                    job = adapter.parse(raw_doc, url)
                except Exception:
                    logger.exception("validated_feed_parse_failed", extra={"source": adapter.source_name, "url": url})
                    continue
                raw_documents[url] = raw_doc
                normalized_jobs.append(job)

            if not normalized_jobs:
                continue
            self.store.record_discovery_documents(
                source_name=adapter.source_name,
                discovered_urls=list(raw_documents.keys()),
                raw_documents=raw_documents,
                normalized_jobs=normalized_jobs,
                next_cursor=adapter.next_cursor(),
            )
            discovered_count += len(normalized_jobs)

        return discovered_count

    def _validate_token(self, *, provider: str, token: str) -> tuple[str, str | None]:
        url_by_provider = {
            "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=false",
            "lever": f"https://api.lever.co/v0/postings/{token}?mode=json",
            "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1&offset=0",
        }
        url = url_by_provider.get(provider)
        if not url:
            return "invalid", f"unsupported_provider:{provider}"

        try:
            response = self.http_client.get(url, timeout=self.timeout_seconds)
        except Exception as exc:
            return "pending", f"request_failed:{exc.__class__.__name__}"

        if response.status_code == 404:
            return "invalid", "not_found"
        if response.status_code in {429, 500, 502, 503, 504}:
            return "pending", f"transient_http_{response.status_code}"
        if response.status_code >= 400:
            return "invalid", f"http_{response.status_code}"
        return "validated", None
