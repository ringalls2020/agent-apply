from __future__ import annotations

import logging
import os
import time

from sqlalchemy import delete

from common.time import utc_now

from cloud_automation.db import (
    create_db_engine,
    create_session_factory,
    ensure_runtime_indexes,
    get_database_url,
)
from cloud_automation.db_models import ArtifactRefRow
from cloud_automation.logging_config import configure_logging

logger = logging.getLogger(__name__)


def run() -> None:
    configure_logging()
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    session_factory = create_session_factory(engine)
    interval = int(os.getenv("MAINTENANCE_INTERVAL_SECONDS", "3600"))

    logger.info("maintenance_worker_started", extra={"interval_seconds": interval})
    while True:
        cutoff = utc_now()
        with session_factory() as session:
            session.execute(
                delete(ArtifactRefRow).where(ArtifactRefRow.expires_at.is_not(None), ArtifactRefRow.expires_at < cutoff)
            )
            session.commit()
        time.sleep(max(interval, 60))


if __name__ == "__main__":
    run()
