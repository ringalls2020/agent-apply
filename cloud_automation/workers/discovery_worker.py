from __future__ import annotations

import logging
import os
import time

from cloud_automation.adapters import build_configured_adapters
from cloud_automation.db import create_db_engine, create_session_factory, get_database_url
from cloud_automation.services import DiscoveryCoordinator, JobIntelStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def run() -> None:
    engine = create_db_engine(get_database_url())
    store = JobIntelStore(create_session_factory(engine))
    coordinator = DiscoveryCoordinator(store=store, adapters=build_configured_adapters())
    interval = int(os.getenv("DISCOVERY_INTERVAL_SECONDS", "21600"))

    logger.info("discovery_worker_started", extra={"interval_seconds": interval})
    while True:
        coordinator.run_discovery_once()
        time.sleep(max(interval, 60))


if __name__ == "__main__":
    run()
