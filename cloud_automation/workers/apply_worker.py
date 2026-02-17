from __future__ import annotations

import logging
import os
import time

from sqlalchemy import select

from cloud_automation.db import create_db_engine, create_session_factory, get_database_url
from cloud_automation.db_models import ApplyRunRow
from cloud_automation.models import MatchRunStatus
from cloud_automation.services import ApplyService, CallbackEmitter, JobIntelStore

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def run() -> None:
    engine = create_db_engine(get_database_url())
    session_factory = create_session_factory(engine)
    store = JobIntelStore(session_factory)
    service = ApplyService(store=store, callback_emitter=CallbackEmitter())

    poll_seconds = int(os.getenv("APPLY_WORKER_POLL_SECONDS", "5"))
    logger.info("apply_worker_started", extra={"poll_seconds": poll_seconds})

    while True:
        with session_factory() as session:
            queued = session.scalars(
                select(ApplyRunRow.id).where(ApplyRunRow.status == MatchRunStatus.queued.value)
            ).all()
        for run_id in queued:
            logger.info("apply_worker_processing", extra={"run_id": run_id})
            import asyncio

            asyncio.run(service.execute(run_id))

        time.sleep(max(poll_seconds, 1))


if __name__ == "__main__":
    run()
