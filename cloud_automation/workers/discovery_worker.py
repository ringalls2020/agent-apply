from __future__ import annotations

import logging
import os
import time

import httpx

from cloud_automation.adapters import build_configured_adapters
from cloud_automation.db import (
    create_db_engine,
    create_session_factory,
    ensure_runtime_indexes,
    get_database_url,
)
from cloud_automation.services import DiscoveryCoordinator, JobIntelStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def run() -> None:
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    http_timeout = float(os.getenv("CLOUD_HTTP_TIMEOUT_SECONDS", "20"))
    http_client = httpx.Client(timeout=http_timeout)
    adapters = build_configured_adapters(http_client=http_client)
    store = JobIntelStore(create_session_factory(engine))
    coordinator = DiscoveryCoordinator(store=store, adapters=adapters)
    interval = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "21600"))

    logger.info("discovery_worker_started", extra={"interval_seconds": interval})
    try:
        while True:
            coordinator.run_discovery_once()
            time.sleep(max(interval, 60))
    finally:
        for adapter in adapters:
            close_adapter = getattr(adapter, "close", None)
            if callable(close_adapter):
                close_adapter()
        http_client.close()


if __name__ == "__main__":
    run()
