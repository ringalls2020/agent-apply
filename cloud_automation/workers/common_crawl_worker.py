from __future__ import annotations

import logging
import os
import time

import httpx

from cloud_automation.db import (
    create_db_engine,
    create_session_factory,
    ensure_runtime_indexes,
    get_database_url,
)
from cloud_automation.logging_config import configure_logging
from cloud_automation.services import CommonCrawlCoordinator, JobIntelStore

logger = logging.getLogger(__name__)


def run() -> None:
    configure_logging()
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    http_timeout = float(os.getenv("CLOUD_HTTP_TIMEOUT_SECONDS", "20"))
    http_client = httpx.Client(timeout=http_timeout)
    store = JobIntelStore(create_session_factory(engine))
    coordinator = CommonCrawlCoordinator(store=store, http_client=http_client)
    interval = int(os.getenv("COMMON_CRAWL_INTERVAL_SECONDS", "86400"))

    logger.info("common_crawl_worker_started", extra={"interval_seconds": interval})
    try:
        while True:
            coordinator.run_common_crawl_once()
            time.sleep(max(interval, 300))
    finally:
        http_client.close()


if __name__ == "__main__":
    run()
