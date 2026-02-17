from __future__ import annotations

import asyncio
import logging
import os
import time

from cloud_automation.db import create_db_engine, create_session_factory, get_database_url
from cloud_automation.services import JobIntelStore, MatchingService

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def run() -> None:
    engine = create_db_engine(get_database_url())
    session_factory = create_session_factory(engine)
    store = JobIntelStore(session_factory)
    service = MatchingService(store=store)

    poll_seconds = int(os.getenv("MATCH_WORKER_POLL_SECONDS", "5"))
    logger.info("match_worker_started", extra={"poll_seconds": poll_seconds})

    while True:
        queued = store.list_queued_match_run_ids(limit=50)
        for run_id in queued:
            if not store.claim_match_run(run_id):
                continue
            logger.info("match_worker_processing", extra={"run_id": run_id})
            asyncio.run(service.execute(run_id, assume_claimed=True))

        time.sleep(max(poll_seconds, 1))


if __name__ == "__main__":
    run()
