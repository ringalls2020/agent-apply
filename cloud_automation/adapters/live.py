from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List

import httpx

from cloud_automation.models import NormalizedJob

logger = logging.getLogger(__name__)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class GreenhouseLiveAdapter:
    source_name = "greenhouse"

    def __init__(
        self,
        board_tokens: list[str],
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.board_tokens = [token.strip() for token in board_tokens if token.strip()]
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=self.timeout_seconds)
        self._cache: dict[str, dict[str, Any]] = {}

    def discover(self, seeds: list[str], cursor: str | None = None) -> list[str]:
        del seeds, cursor
        discovered: list[str] = []
        for board in self.board_tokens:
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            try:
                response = self.client.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
            except Exception:
                logger.exception("greenhouse_discovery_failed", extra={"board": board})
                continue

            body = response.json()
            for job in body.get("jobs", []):
                key = f"greenhouse://{board}/{job.get('id')}"
                self._cache[key] = {"board": board, "job": job}
                discovered.append(key)
        return discovered

    def fetch(self, url: str) -> str:
        payload = self._cache.get(url, {})
        return json.dumps(payload, ensure_ascii=True)

    def parse(self, raw: str, url: str) -> NormalizedJob:
        payload = json.loads(raw)
        job = payload.get("job", {})
        board = payload.get("board") or "greenhouse"
        location = (job.get("location") or {}).get("name")
        return NormalizedJob(
            id=f"greenhouse-{board}-{job.get('id')}",
            title=job.get("title") or "Unknown title",
            company=board,
            location=location,
            salary=None,
            apply_url=job.get("absolute_url") or f"https://boards.greenhouse.io/{board}",
            source=self.source_name,
            posted_at=_parse_datetime(job.get("updated_at")),
            description=job.get("content") or "",
        )

    def next_cursor(self) -> str | None:
        return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class LeverLiveAdapter:
    source_name = "lever"

    def __init__(
        self,
        companies: list[str],
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.companies = [company.strip() for company in companies if company.strip()]
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=self.timeout_seconds)
        self._cache: dict[str, dict[str, Any]] = {}

    def discover(self, seeds: list[str], cursor: str | None = None) -> list[str]:
        del seeds, cursor
        discovered: list[str] = []
        for company in self.companies:
            url = f"https://api.lever.co/v0/postings/{company}?mode=json"
            try:
                response = self.client.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
            except Exception:
                logger.exception("lever_discovery_failed", extra={"company": company})
                continue

            postings = response.json()
            for posting in postings:
                posting_id = posting.get("id")
                if not posting_id:
                    continue
                key = f"lever://{company}/{posting_id}"
                self._cache[key] = {"company": company, "posting": posting}
                discovered.append(key)
        return discovered

    def fetch(self, url: str) -> str:
        payload = self._cache.get(url, {})
        return json.dumps(payload, ensure_ascii=True)

    def parse(self, raw: str, url: str) -> NormalizedJob:
        payload = json.loads(raw)
        posting = payload.get("posting", {})
        company = payload.get("company") or "lever"
        categories = posting.get("categories") or {}
        return NormalizedJob(
            id=f"lever-{company}-{posting.get('id')}",
            title=posting.get("text") or "Unknown title",
            company=company,
            location=categories.get("location"),
            salary=None,
            apply_url=posting.get("hostedUrl") or f"https://jobs.lever.co/{company}",
            source=self.source_name,
            posted_at=_parse_datetime(posting.get("createdAt")),
            description=posting.get("descriptionPlain") or posting.get("description") or "",
        )

    def next_cursor(self) -> str | None:
        return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class SmartRecruitersLiveAdapter:
    source_name = "smartrecruiters"

    def __init__(
        self,
        companies: list[str],
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.companies = [company.strip() for company in companies if company.strip()]
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=self.timeout_seconds)
        self._cache: dict[str, dict[str, Any]] = {}

    def discover(self, seeds: list[str], cursor: str | None = None) -> list[str]:
        del seeds, cursor
        discovered: list[str] = []
        for company in self.companies:
            url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=0"
            try:
                response = self.client.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
            except Exception:
                logger.exception(
                    "smartrecruiters_discovery_failed",
                    extra={"company": company},
                )
                continue

            postings = response.json().get("content", [])
            for posting in postings:
                posting_id = posting.get("id")
                if not posting_id:
                    continue
                key = f"smartrecruiters://{company}/{posting_id}"
                self._cache[key] = {"company": company, "posting": posting}
                discovered.append(key)
        return discovered

    def fetch(self, url: str) -> str:
        payload = self._cache.get(url, {})
        return json.dumps(payload, ensure_ascii=True)

    def parse(self, raw: str, url: str) -> NormalizedJob:
        payload = json.loads(raw)
        posting = payload.get("posting", {})
        company = payload.get("company") or "smartrecruiters"
        posting_id = posting.get("id") or "unknown"
        location_obj = posting.get("location") or {}

        return NormalizedJob(
            id=f"smartrecruiters-{company}-{posting_id}",
            title=posting.get("name") or "Unknown title",
            company=company,
            location=location_obj.get("city") or location_obj.get("region") or "United States",
            salary=None,
            apply_url=f"https://jobs.smartrecruiters.com/{company}/{posting_id}",
            source=self.source_name,
            posted_at=_parse_datetime(posting.get("releasedDate")),
            description=posting.get("jobAd", {}).get("sections", {}).get("jobDescription", ""),
        )

    def next_cursor(self) -> str | None:
        return None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
