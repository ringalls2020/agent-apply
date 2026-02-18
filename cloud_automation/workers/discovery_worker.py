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
from cloud_automation.services import DiscoveryCoordinator, JobIntelStore

logger = logging.getLogger(__name__)


def run() -> None:
    configure_logging()
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    http_timeout = float(os.getenv("CLOUD_HTTP_TIMEOUT_SECONDS", "20"))
    http_client = httpx.Client(timeout=http_timeout)
    store = JobIntelStore(create_session_factory(engine))
    coordinator = DiscoveryCoordinator(store=store, http_client=http_client)
    interval = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "21600"))
    loop_sleep_seconds = max(int(os.getenv("DISCOVERY_WORKER_LOOP_SLEEP_SECONDS", "15")), 1)
    last_scheduled_run_at = 0.0

    logger.info(
        "discovery_worker_started",
        extra={"interval_seconds": interval, "loop_sleep_seconds": loop_sleep_seconds},
    )
    try:
        while True:
            queued_ids = store.list_queued_discovery_refresh_ids(limit=20)
            for request_id in queued_ids:
                if not store.claim_discovery_refresh_request(request_id):
                    continue
                succeeded = coordinator.run_discovery_once()
                if not succeeded:
                    store.finalize_discovery_refresh_request(
                        request_id=request_id,
                        error="discovery_failed",
                    )
                    logger.error(
                        "discovery_refresh_request_failed",
                        extra={"request_id": request_id},
                    )
                else:
                    store.finalize_discovery_refresh_request(
                        request_id=request_id,
                        error=None,
                    )
                    logger.info(
                        "discovery_refresh_request_completed",
                        extra={"request_id": request_id},
                    )

            now = time.monotonic()
            if now - last_scheduled_run_at >= max(interval, 60):
                coordinator.run_discovery_once()
                last_scheduled_run_at = time.monotonic()

            time.sleep(loop_sleep_seconds)
    finally:
        http_client.close()


if __name__ == "__main__":
    run()
