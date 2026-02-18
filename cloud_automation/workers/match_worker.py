from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import time

from cloud_automation.db import (
    create_db_engine,
    create_session_factory,
    ensure_runtime_indexes,
    get_database_url,
)
from cloud_automation.logging_config import configure_logging
from cloud_automation.services import JobIntelStore, MatchingService

logger = logging.getLogger(__name__)


def _process_run(*, run_id: str, store: JobIntelStore, service: MatchingService) -> None:
    if not store.claim_match_run(run_id):
        return
    logger.info("match_worker_processing", extra={"run_id": run_id})
    asyncio.run(service.execute(run_id, assume_claimed=True))


def run() -> None:
    configure_logging()
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    session_factory = create_session_factory(engine)
    store = JobIntelStore(session_factory)
    service = MatchingService(store=store)

    poll_seconds = int(os.getenv("MATCH_WORKER_POLL_SECONDS", "5"))
    concurrency = max(1, int(os.getenv("MATCH_WORKER_CONCURRENCY", "1")))
    logger.info(
        "match_worker_started",
        extra={"poll_seconds": poll_seconds, "concurrency": concurrency},
    )

    while True:
        queued = store.list_queued_match_run_ids(limit=max(50, concurrency * 5))
        if queued:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(
                        _process_run,
                        run_id=run_id,
                        store=store,
                        service=service,
                    )
                    for run_id in queued
                ]
                for future in as_completed(futures):
                    future.result()

        time.sleep(max(poll_seconds, 1))


if __name__ == "__main__":
    run()
