from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from cloud_automation.services import ApplyService, CallbackEmitter, JobIntelStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def _process_run(*, run_id: str, store: JobIntelStore, service: ApplyService) -> None:
    if not store.claim_apply_run(run_id):
        return
    logger.info("apply_worker_processing", extra={"run_id": run_id})
    asyncio.run(service.execute(run_id, assume_claimed=True))


def run() -> None:
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    session_factory = create_session_factory(engine)
    store = JobIntelStore(session_factory)
    http_timeout = float(os.getenv("CLOUD_HTTP_TIMEOUT_SECONDS", "20"))
    http_client = httpx.Client(timeout=http_timeout)
    callback_emitter = CallbackEmitter(http_client=http_client)
    service = ApplyService(
        store=store,
        callback_emitter=callback_emitter,
        http_client=http_client,
    )

    poll_seconds = int(os.getenv("APPLY_WORKER_POLL_SECONDS", "5"))
    concurrency = max(1, int(os.getenv("APPLY_WORKER_CONCURRENCY", "1")))
    logger.info(
        "apply_worker_started",
        extra={"poll_seconds": poll_seconds, "concurrency": concurrency},
    )

    try:
        while True:
            queued = store.list_queued_apply_run_ids(limit=max(50, concurrency * 5))
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
    finally:
        close_service = getattr(service, "close", None)
        if callable(close_service):
            close_service()
        http_client.close()


if __name__ == "__main__":
    run()
