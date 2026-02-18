from __future__ import annotations

import json
import logging
import os
from urllib.parse import quote_plus

import httpx

from .ats_token_utils import extract_ats_tokens_from_values
from .job_store import JobIntelStore

logger = logging.getLogger(__name__)


COMMON_CRAWL_PATTERNS = (
    "boards.greenhouse.io/embed/job_board/js?for=",
    "boards.greenhouse.io/",
    "jobs.lever.co/",
    "api.smartrecruiters.com/v1/companies/",
)


class CommonCrawlPipeline:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        http_client: httpx.Client,
    ) -> None:
        self.store = store
        self.http_client = http_client
        self.timeout_seconds = float(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "20"))
        self.lookback_indexes = max(int(os.getenv("COMMON_CRAWL_LOOKBACK_INDEXES", "2")), 1)
        self.max_pages_per_pattern = max(int(os.getenv("COMMON_CRAWL_MAX_PAGES_PER_PATTERN", "3")), 1)
        self.max_records_per_pattern = max(
            int(os.getenv("COMMON_CRAWL_MAX_RECORDS_PER_PATTERN", "1500")),
            1,
        )
        self.user_agent = os.getenv(
            "DISCOVERY_USER_AGENT",
            "agent-apply-common-crawl-bot/1.0",
        ).strip()

    def run_method_b(self) -> int:
        collections = self._fetch_recent_collections()
        if not collections:
            logger.warning("common_crawl_no_collections")
            return 0

        inserted = 0
        for collection in collections:
            for pattern in COMMON_CRAWL_PATTERNS:
                inserted += self._extract_tokens_for_pattern(collection=collection, pattern=pattern)
        return inserted

    def _fetch_recent_collections(self) -> list[str]:
        response = self.http_client.get(
            "https://index.commoncrawl.org/collinfo.json",
            headers={"user-agent": self.user_agent, "accept": "application/json"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        ids = [str(item.get("id", "")).strip() for item in body if isinstance(item, dict)]
        ids = [item for item in ids if item]
        ids.sort(reverse=True)
        return ids[: self.lookback_indexes]

    def _extract_tokens_for_pattern(self, *, collection: str, pattern: str) -> int:
        inserted = 0
        seen_records = 0
        query_pattern = quote_plus(f"*{pattern}*")
        for page in range(self.max_pages_per_pattern):
            index_url = (
                f"https://index.commoncrawl.org/{collection}-index"
                f"?url={query_pattern}&output=json&page={page}"
            )
            response = self.http_client.get(
                index_url,
                headers={"user-agent": self.user_agent, "accept": "application/json,text/plain;q=0.9"},
                timeout=self.timeout_seconds,
            )
            if response.status_code >= 400:
                break

            lines = [line.strip() for line in response.text.splitlines() if line.strip()]
            if not lines:
                break

            for line in lines:
                if seen_records >= self.max_records_per_pattern:
                    return inserted
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                record_url = str(record.get("url", "")).strip()
                if not record_url:
                    continue
                tokens = extract_ats_tokens_from_values((record_url,))
                if not tokens:
                    continue
                inserted += self.store.record_extracted_tokens(
                    extracted_tokens=tokens,
                    method="method_b",
                    evidence_url=record_url,
                )
                seen_records += 1
        return inserted
