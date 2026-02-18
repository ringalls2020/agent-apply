from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable

from .models import MatchRunStatus, MatchRunStatusResponse


TERMINAL_MATCH_STATUSES = {
    MatchRunStatus.completed,
    MatchRunStatus.partial,
    MatchRunStatus.failed,
}


async def wait_for_match_terminal_status(
    *,
    get_status: Callable[[], MatchRunStatusResponse],
    poll_interval_seconds: float,
    poll_max_attempts: int,
) -> MatchRunStatusResponse | None:
    latest_status: MatchRunStatusResponse | None = None
    for attempt_index in range(poll_max_attempts):
        latest_status = get_status()
        if latest_status.status in TERMINAL_MATCH_STATUSES:
            return latest_status
        if attempt_index + 1 < poll_max_attempts:
            await asyncio.sleep(max(0.05, poll_interval_seconds))
    return latest_status


def resolve_discovered_anchor(
    *,
    posted_at: datetime | None,
    existing_discovered_at: datetime | None,
    now: datetime,
) -> datetime:
    if posted_at is not None:
        return posted_at
    if existing_discovered_at is not None:
        return existing_discovered_at
    return now
