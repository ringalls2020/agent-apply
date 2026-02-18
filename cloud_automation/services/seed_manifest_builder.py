from __future__ import annotations

import logging
import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .discovery_pipeline import DiscoveryPipeline
from .job_store import JobIntelStore

logger = logging.getLogger(__name__)

_DEFAULT_SEED_SOURCE_PAGES = [
    "https://remoteintech.company/companies/",
    "https://remoteintech.company/browse/worldwide/",
    "https://remoteintech.company/browse/americas/",
    "https://remoteintech.company/browse/europe/",
    "https://remoteintech.company/browse/fully-remote/",
    "https://remoteintech.company/browse/remote-first/",
    "https://remoteintech.company/browse/remote-friendly/",
    "https://remoteintech.company/browse/hybrid/",
    "https://remoteintech.company/browse/python/",
    "https://remoteintech.company/browse/javascript/",
]

_CAREERS_KEYWORDS = (
    "careers",
    "career",
    "jobs",
    "job",
    "join",
    "work-with-us",
    "vacancies",
    "positions",
)

_KNOWN_ATS_HOST_FRAGMENTS = (
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.smartrecruiters.com",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "job-boards.greenhouse.io",
)

_SOCIAL_HOST_FRAGMENTS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "tiktok.com",
)


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value for key, value in attrs if isinstance(key, str)}
        href = attr_map.get("href")
        if href:
            self._current_href = str(href)
            self._text_chunks = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        label = " ".join(chunk.strip() for chunk in self._text_chunks if chunk.strip()).strip()
        self.links.append((self._current_href, label))
        self._current_href = None
        self._text_chunks = []


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_url(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if not parts.netloc:
        return None

    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"gclid", "fbclid", "ref", "source"}
    ]
    clean_query = urlencode(query_items, doseq=True)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, clean_query, ""))


def _looks_like_careers_link(*, normalized_url: str, label: str, source_host: str) -> bool:
    parts = urlsplit(normalized_url)
    host = (parts.hostname or "").lower()
    path = (parts.path or "").lower()
    text = label.strip().lower()

    if not host or host == source_host:
        return False
    if any(fragment in host for fragment in _SOCIAL_HOST_FRAGMENTS):
        return False
    if "remoteintech.company" in host:
        return False

    if any(fragment in host for fragment in _KNOWN_ATS_HOST_FRAGMENTS):
        return True
    if any(keyword in path for keyword in _CAREERS_KEYWORDS):
        return True
    if any(keyword in text for keyword in _CAREERS_KEYWORDS):
        return True
    return False


def _extract_company_label(label: str) -> str | None:
    text = re.sub(r"\s+", " ", label).strip()
    if not text:
        return None
    for keyword in _CAREERS_KEYWORDS:
        text = re.sub(rf"\b{re.escape(keyword)}\b", "", text, flags=re.IGNORECASE)
    text = text.strip(" -|:")
    return text or None


class SeedManifestBuilder:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        http_client: httpx.Client,
    ) -> None:
        self.store = store
        self.http_client = http_client
        self.source_page_urls = _csv_env("SEED_SOURCE_PAGE_URLS") or list(_DEFAULT_SEED_SOURCE_PAGES)
        self.pipeline_helper = DiscoveryPipeline(store=store, http_client=http_client)
        self.max_retries = max(int(os.getenv("SEED_MANIFEST_MAX_RETRIES", "3")), 1)
        self.timeout_seconds = max(float(os.getenv("SEED_MANIFEST_TIMEOUT_SECONDS", "20")), 1.0)
        # Reuse discovery crawling helpers, but with manifest-builder-specific reliability settings.
        self.pipeline_helper.max_retries = self.max_retries
        self.pipeline_helper.timeout_seconds = self.timeout_seconds

    def run_build_once(self) -> tuple[int, int]:
        run_id = self.store.create_seed_manifest_build_run(source_count=len(self.source_page_urls))
        discovered_entries: dict[str, tuple[str | None, str, str]] = {}
        discovered_link_count = 0
        logger.info(
            "seed_manifest_build_started",
            extra={
                "run_id": run_id,
                "source_count": len(self.source_page_urls),
                "max_retries": self.max_retries,
                "timeout_seconds": self.timeout_seconds,
            },
        )
        try:
            for source_index, source_page_url in enumerate(self.source_page_urls, start=1):
                source_page_url = source_page_url.strip()
                if not source_page_url:
                    continue
                logger.info(
                    "seed_manifest_source_processing_started",
                    extra={
                        "run_id": run_id,
                        "source_page_url": source_page_url,
                        "source_index": source_index,
                        "source_count": len(self.source_page_urls),
                    },
                )
                discovered = self._extract_from_source_page(source_page_url, run_id=run_id)
                discovered_link_count += len(discovered)
                for company, careers_url in discovered:
                    discovered_entries[careers_url] = (company, careers_url, source_page_url)
                logger.info(
                    "seed_manifest_source_processing_completed",
                    extra={
                        "run_id": run_id,
                        "source_page_url": source_page_url,
                        "retained_careers_links": len(discovered),
                    },
                )

            retained_count = self.store.replace_seed_manifest_entries(
                entries=discovered_entries.values()
            )
            self.store.finalize_seed_manifest_build_run(
                run_id=run_id,
                discovered_link_count=discovered_link_count,
                retained_count=retained_count,
                error=None,
            )
            logger.info(
                "seed_manifest_build_completed",
                extra={
                    "run_id": run_id,
                    "source_count": len(self.source_page_urls),
                    "discovered_link_count": discovered_link_count,
                    "retained_count": retained_count,
                },
            )
            return discovered_link_count, retained_count
        except Exception as exc:
            self.store.finalize_seed_manifest_build_run(
                run_id=run_id,
                discovered_link_count=discovered_link_count,
                retained_count=0,
                error=str(exc),
            )
            logger.exception("seed_manifest_build_failed", extra={"run_id": run_id})
            raise

    def _extract_from_source_page(
        self,
        source_page_url: str,
        *,
        run_id: str,
    ) -> list[tuple[str | None, str]]:
        started_at = time.perf_counter()
        parsed_source = urlsplit(source_page_url)
        source_host = (parsed_source.hostname or "").lower()
        if not source_host:
            logger.warning(
                "seed_manifest_source_invalid_url",
                extra={"run_id": run_id, "source_page_url": source_page_url},
            )
            return []

        robots = self.pipeline_helper._resolve_robots(domain=source_host, target_url=source_page_url)
        logger.info(
            "seed_manifest_source_robots_resolved",
            extra={
                "run_id": run_id,
                "source_page_url": source_page_url,
                "source_host": source_host,
                "robots_allowed": robots.allowed,
                "crawl_delay_seconds": robots.crawl_delay_seconds,
                "robots_error": robots.error,
            },
        )
        if not robots.allowed:
            logger.warning(
                "seed_manifest_source_skipped_by_robots",
                extra={
                    "run_id": run_id,
                    "source_page_url": source_page_url,
                    "error": robots.error,
                },
            )
            return []

        crawl_delay = robots.crawl_delay_seconds
        if crawl_delay is None:
            crawl_delay = self.pipeline_helper.default_crawl_delay_seconds
        self.pipeline_helper._respect_domain_delay(domain=source_host, crawl_delay_seconds=crawl_delay)

        logger.info(
            "seed_manifest_source_fetch_started",
            extra={
                "run_id": run_id,
                "source_page_url": source_page_url,
                "source_host": source_host,
            },
        )
        response = self.pipeline_helper._get_with_backoff(
            source_page_url,
            headers={
                "user-agent": self.pipeline_helper.user_agent,
                "accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            },
        )
        if response is None or response.status_code >= 400:
            logger.warning(
                "seed_manifest_source_fetch_failed",
                extra={
                    "run_id": run_id,
                    "source_page_url": source_page_url,
                    "status_code": getattr(response, "status_code", None),
                },
            )
            return []

        parser = _AnchorCollector()
        parser.feed(response.text)

        discovered: dict[str, tuple[str | None, str]] = {}
        for raw_href, label in parser.links:
            href = raw_href.strip()
            if not href:
                continue
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute_url = urljoin(source_page_url, href)
            normalized = _normalize_url(absolute_url)
            if not normalized:
                continue
            if not _looks_like_careers_link(
                normalized_url=normalized,
                label=label,
                source_host=source_host,
            ):
                continue
            discovered[normalized] = (_extract_company_label(label), normalized)

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "seed_manifest_source_extraction_completed",
            extra={
                "run_id": run_id,
                "source_page_url": source_page_url,
                "status_code": response.status_code,
                "anchors_seen": len(parser.links),
                "retained_careers_links": len(discovered),
                "duration_ms": duration_ms,
            },
        )
        return list(discovered.values())
