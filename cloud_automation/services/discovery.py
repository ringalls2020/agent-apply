from __future__ import annotations

import logging

import httpx

from .common_crawl_pipeline import CommonCrawlPipeline
from .discovery_pipeline import DiscoveryPipeline
from .job_store import JobIntelStore
from .token_registry import TokenRegistryCoordinator

logger = logging.getLogger(__name__)


class DiscoveryCoordinator:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        http_client: httpx.Client,
    ) -> None:
        self.store = store
        self.method_a = DiscoveryPipeline(store=store, http_client=http_client)
        self.registry = TokenRegistryCoordinator(store=store, http_client=http_client)

    def run_discovery_once(self) -> None:
        crawl_id = self.store.create_crawl_run(source_count=3)
        discovered_count = 0
        try:
            extracted_count = self.method_a.run_method_a()
            validation_stats = self.registry.validate_tokens_once()
            discovered_count = self.registry.ingest_validated_jobs_once()
            logger.info(
                "method_a_discovery_completed",
                extra={
                    "crawl_id": crawl_id,
                    "tokens_extracted": extracted_count,
                    "jobs_discovered": discovered_count,
                    "validation_stats": validation_stats,
                },
            )
            self.store.finalize_crawl_run(
                run_id=crawl_id,
                discovered_count=discovered_count,
                error=None,
            )
        except Exception as exc:
            logger.exception("method_a_discovery_failed", extra={"crawl_id": crawl_id})
            self.store.finalize_crawl_run(
                run_id=crawl_id,
                discovered_count=discovered_count,
                error=str(exc),
            )


class CommonCrawlCoordinator:
    def __init__(
        self,
        *,
        store: JobIntelStore,
        http_client: httpx.Client,
    ) -> None:
        self.store = store
        self.method_b = CommonCrawlPipeline(store=store, http_client=http_client)
        self.registry = TokenRegistryCoordinator(store=store, http_client=http_client)

    def run_common_crawl_once(self) -> None:
        crawl_id = self.store.create_crawl_run(source_count=1)
        discovered_count = 0
        try:
            discovered_count = self.method_b.run_method_b()
            validation_stats = self.registry.validate_tokens_once()
            logger.info(
                "method_b_common_crawl_completed",
                extra={
                    "crawl_id": crawl_id,
                    "tokens_extracted": discovered_count,
                    "validation_stats": validation_stats,
                },
            )
            self.store.finalize_crawl_run(
                run_id=crawl_id,
                discovered_count=discovered_count,
                error=None,
            )
        except Exception as exc:
            logger.exception("method_b_common_crawl_failed", extra={"crawl_id": crawl_id})
            self.store.finalize_crawl_run(
                run_id=crawl_id,
                discovered_count=discovered_count,
                error=str(exc),
            )
