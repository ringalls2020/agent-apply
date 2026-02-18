from __future__ import annotations

import csv
import io
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from common.time import utc_now

from ..security import create_hs256_jwt
from .ats_token_utils import extract_ats_tokens_from_text
from .job_store import JobIntelStore

logger = logging.getLogger(__name__)


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class _RobotsDecision:
    allowed: bool
    crawl_delay_seconds: int | None
    error: str | None = None


class DiscoveryPipeline:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        http_client: httpx.Client,
    ) -> None:
        self.store = store
        self.http_client = http_client
        self.seed_manifest_urls = _csv_env("SEED_MANIFEST_URLS")
        self.seed_manifest_require_service_jwt = (
            os.getenv("SEED_MANIFEST_REQUIRE_SERVICE_JWT", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.seed_manifest_service_issuer = os.getenv(
            "SEED_MANIFEST_SERVICE_ISSUER",
            os.getenv("CLOUD_AUTOMATION_SERVICE_ID", "job-intel-api"),
        ).strip() or "job-intel-api"
        self.seed_manifest_service_audience = os.getenv(
            "SEED_MANIFEST_SERVICE_AUDIENCE",
            os.getenv("CLOUD_AUTOMATION_EXPECTED_AUDIENCE", "job-intel-api"),
        ).strip() or "job-intel-api"
        self.seed_manifest_signing_secret = os.getenv(
            "SEED_MANIFEST_SERVICE_SIGNING_SECRET",
            os.getenv("CLOUD_AUTOMATION_SIGNING_SECRET", "dev-cloud-signing-secret"),
        )
        self.timeout_seconds = float(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "20"))
        self.max_retries = max(int(os.getenv("DISCOVERY_MAX_RETRIES", "3")), 1)
        self.default_crawl_delay_seconds = max(
            int(os.getenv("DISCOVERY_DEFAULT_CRAWL_DELAY_SECONDS", "2")),
            0,
        )
        self.robots_ttl_seconds = max(int(os.getenv("DISCOVERY_ROBOTS_TTL_SECONDS", "21600")), 60)
        ua = os.getenv("DISCOVERY_USER_AGENT", "").strip()
        contact = os.getenv("DISCOVERY_CONTACT_EMAIL", "").strip()
        if not ua:
            ua = "agent-apply-discovery-bot/1.0"
        if contact and contact not in ua:
            ua = f"{ua} (+mailto:{contact})"
        self.user_agent = ua
        self._last_domain_request_at: dict[str, float] = {}

    def run_method_a(self) -> int:
        if not self.seed_manifest_urls:
            logger.info("method_a_no_seed_manifests_configured")
            return 0

        refreshed_seed_count = 0
        for manifest_url in self.seed_manifest_urls:
            seeds = self._fetch_manifest_seeds(manifest_url)
            if seeds:
                self.store.upsert_discovery_seeds(manifest_url=manifest_url, seeds=seeds)
                refreshed_seed_count += len(seeds)

        extracted_total = 0
        seeds = self.store.list_discovery_seeds()
        if refreshed_seed_count == 0 and seeds:
            logger.info(
                "method_a_using_cached_discovery_seeds",
                extra={"seed_count": len(seeds)},
            )
        for seed in seeds:
            extracted_total += self._crawl_seed(seed)
        return extracted_total

    def _fetch_manifest_seeds(self, manifest_url: str) -> list[tuple[str | None, str]]:
        headers = {
            "user-agent": self.user_agent,
            "accept": "application/json, text/csv, text/plain;q=0.9, */*;q=0.1",
        }
        headers.update(self._seed_manifest_auth_headers())
        logger.info(
            "seed_manifest_fetch_started",
            extra={
                "manifest_url": manifest_url,
                "auth_enabled": self.seed_manifest_require_service_jwt,
            },
        )
        response = self._get_with_backoff(
            manifest_url,
            headers=headers,
        )
        if response is None or response.status_code >= 400:
            logger.warning(
                "seed_manifest_fetch_failed",
                extra={"manifest_url": manifest_url, "status_code": getattr(response, "status_code", None)},
            )
            return []
        body = response.text
        parsed = self._parse_manifest(body)
        logger.info(
            "seed_manifest_fetch_completed",
            extra={
                "manifest_url": manifest_url,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "seed_count": len(parsed),
            },
        )
        return parsed

    @staticmethod
    def _parse_manifest(body: str) -> list[tuple[str | None, str]]:
        body = (body or "").strip()
        if not body:
            return []

        try:
            payload = json.loads(body)
            if isinstance(payload, list):
                parsed: list[tuple[str | None, str]] = []
                for item in payload:
                    if isinstance(item, str):
                        parsed.append((None, item))
                        continue
                    if not isinstance(item, dict):
                        continue
                    url = str(
                        item.get("careers_url")
                        or item.get("url")
                        or item.get("careersUrl")
                        or ""
                    ).strip()
                    if url:
                        company = item.get("company")
                        parsed.append((str(company).strip() if company else None, url))
                if parsed:
                    return parsed
        except json.JSONDecodeError:
            pass

        parsed_csv = DiscoveryPipeline._parse_csv_manifest(body)
        if parsed_csv:
            return parsed_csv

        # Fallback: newline-delimited URLs.
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        return [(None, line) for line in lines]

    def _seed_manifest_auth_headers(self) -> dict[str, str]:
        if not self.seed_manifest_require_service_jwt:
            return {}
        token = create_hs256_jwt(
            payload={"sub": self.seed_manifest_service_issuer},
            secret=self.seed_manifest_signing_secret,
            issuer=self.seed_manifest_service_issuer,
            audience=self.seed_manifest_service_audience,
            expires_in_seconds=300,
        )
        return {
            "authorization": f"Bearer {token}",
            "x-service-id": self.seed_manifest_service_issuer,
        }

    @staticmethod
    def _parse_csv_manifest(body: str) -> list[tuple[str | None, str]]:
        parsed: list[tuple[str | None, str]] = []
        try:
            reader = csv.DictReader(io.StringIO(body))
        except csv.Error:
            return []
        if not reader.fieldnames:
            return []
        lower_fields = {field.lower() for field in reader.fieldnames if field}
        if not ({"careers_url", "url"} & lower_fields):
            return []
        for row in reader:
            url = (
                str(row.get("careers_url", "")).strip()
                or str(row.get("url", "")).strip()
                or str(row.get("careersUrl", "")).strip()
            )
            if not url:
                continue
            company = str(row.get("company", "")).strip() or None
            parsed.append((company, url))
        return parsed

    def _crawl_seed(self, seed) -> int:
        url = seed.careers_url
        domain = (urlsplit(url).hostname or "").lower()
        if not domain:
            self.store.mark_discovery_seed_result(
                careers_url=url,
                status="error",
                error="invalid_domain",
            )
            return 0

        robots = self._resolve_robots(domain=domain, target_url=url)
        if not robots.allowed:
            status = "robots_blocked" if robots.error is None else "robots_error"
            self.store.mark_discovery_seed_result(
                careers_url=url,
                status=status,
                error=robots.error or "disallowed_by_robots",
            )
            return 0

        crawl_delay = robots.crawl_delay_seconds
        if crawl_delay is None:
            crawl_delay = self.default_crawl_delay_seconds
        self._respect_domain_delay(domain=domain, crawl_delay_seconds=crawl_delay)

        headers = {
            "user-agent": self.user_agent,
            "accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }
        if seed.etag:
            headers["if-none-match"] = seed.etag
        if seed.last_modified:
            headers["if-modified-since"] = seed.last_modified

        response = self._get_with_backoff(url, headers=headers)
        if response is None:
            self.store.mark_discovery_seed_result(
                careers_url=url,
                status="error",
                error="fetch_failed",
            )
            return 0
        if response.status_code == 304:
            self.store.mark_discovery_seed_result(
                careers_url=url,
                status="not_modified",
                etag=seed.etag,
                last_modified=seed.last_modified,
                error=None,
            )
            return 0
        if response.status_code >= 400:
            self.store.mark_discovery_seed_result(
                careers_url=url,
                status="error",
                error=f"http_{response.status_code}",
            )
            return 0

        content = response.text
        extracted_tokens = extract_ats_tokens_from_text(content)
        inserted = self.store.record_extracted_tokens(
            extracted_tokens=extracted_tokens,
            method="method_a",
            evidence_url=url,
        )
        self.store.mark_discovery_seed_result(
            careers_url=url,
            status="ok",
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            error=None,
        )
        return inserted

    def _resolve_robots(self, *, domain: str, target_url: str) -> _RobotsDecision:
        cached = self.store.get_domain_robots_cache(domain=domain)
        now = utc_now()
        if cached is not None and cached.expires_at is not None and cached.expires_at > now:
            if cached.status != "ok":
                if cached.status == "disallow":
                    return _RobotsDecision(
                        allowed=False,
                        crawl_delay_seconds=cached.crawl_delay_seconds,
                        error=None,
                    )
                return _RobotsDecision(
                    allowed=False,
                    crawl_delay_seconds=cached.crawl_delay_seconds,
                    error=cached.last_error or "cached_robots_error",
                )
            if not self._can_fetch_from_robots(
                robots_txt=cached.robots_txt,
                user_agent=self.user_agent,
                target_url=target_url,
            ):
                return _RobotsDecision(
                    allowed=False,
                    crawl_delay_seconds=cached.crawl_delay_seconds,
                    error=None,
                )
            return _RobotsDecision(
                allowed=True,
                crawl_delay_seconds=cached.crawl_delay_seconds,
            )

        robots_url = f"https://{domain}/robots.txt"
        response = self._get_with_backoff(
            robots_url,
            headers={"user-agent": self.user_agent, "accept": "text/plain,*/*;q=0.5"},
        )
        if response is None or response.status_code >= 400:
            self.store.upsert_domain_robots_cache(
                domain=domain,
                robots_url=robots_url,
                robots_txt="",
                crawl_delay_seconds=None,
                status="error",
                error=f"http_{getattr(response, 'status_code', 'fetch_failed')}",
                ttl_seconds=self.robots_ttl_seconds,
            )
            return _RobotsDecision(allowed=False, crawl_delay_seconds=None, error="robots_fetch_failed")

        robots_txt = response.text
        if "user-agent" not in robots_txt.lower():
            self.store.upsert_domain_robots_cache(
                domain=domain,
                robots_url=robots_url,
                robots_txt=robots_txt,
                crawl_delay_seconds=None,
                status="error",
                error="robots_parse_failed",
                ttl_seconds=self.robots_ttl_seconds,
            )
            return _RobotsDecision(allowed=False, crawl_delay_seconds=None, error="robots_parse_failed")

        parser = RobotFileParser()
        parser.parse(robots_txt.splitlines())
        crawl_delay = parser.crawl_delay(self.user_agent)
        if crawl_delay is None:
            crawl_delay = parser.crawl_delay("*")
        allowed = self._can_fetch_from_robots(
            robots_txt=robots_txt,
            user_agent=self.user_agent,
            target_url=target_url,
        )
        status = "ok" if allowed else "disallow"
        self.store.upsert_domain_robots_cache(
            domain=domain,
            robots_url=robots_url,
            robots_txt=robots_txt,
            crawl_delay_seconds=crawl_delay,
            status=status,
            error=None if allowed else "disallowed_by_robots",
            ttl_seconds=self.robots_ttl_seconds,
        )
        return _RobotsDecision(
            allowed=allowed,
            crawl_delay_seconds=crawl_delay,
            error=None,
        )

    @staticmethod
    def _can_fetch_from_robots(*, robots_txt: str, user_agent: str, target_url: str) -> bool:
        parser = RobotFileParser()
        parser.parse(robots_txt.splitlines())
        try:
            return bool(parser.can_fetch(user_agent, target_url))
        except Exception:
            return False

    def _respect_domain_delay(self, *, domain: str, crawl_delay_seconds: int) -> None:
        now = time.monotonic()
        last = self._last_domain_request_at.get(domain)
        delay = max(crawl_delay_seconds, 0)
        if last is not None and delay > 0:
            elapsed = now - last
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_domain_request_at[domain] = time.monotonic()

    def _get_with_backoff(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response | None:
        for attempt in range(self.max_retries):
            try:
                response = self.http_client.get(
                    url,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.max_retries:
                    delay = (2**attempt) + random.uniform(0, 0.25)
                    logger.warning(
                        "http_backoff_retry_scheduled",
                        extra={
                            "url": url,
                            "status_code": response.status_code,
                            "attempt": attempt + 1,
                            "max_retries": self.max_retries,
                            "delay_seconds": round(delay, 3),
                        },
                    )
                    time.sleep(delay)
                    continue
                return response
            except Exception as exc:
                if attempt + 1 >= self.max_retries:
                    logger.warning(
                        "http_backoff_failed",
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "max_retries": self.max_retries,
                            "error": str(exc),
                        },
                    )
                    return None
                delay = (2**attempt) + random.uniform(0, 0.25)
                logger.warning(
                    "http_backoff_retry_scheduled",
                    extra={
                        "url": url,
                        "status_code": None,
                        "attempt": attempt + 1,
                        "max_retries": self.max_retries,
                        "delay_seconds": round(delay, 3),
                        "error": str(exc),
                    },
                )
                time.sleep(delay)
        return None
