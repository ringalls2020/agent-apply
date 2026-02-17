from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return a UTC timestamp as naive datetime for DB compatibility."""
    return datetime.now(UTC).replace(tzinfo=None)


def utc_epoch_seconds() -> int:
    return int(datetime.now(UTC).timestamp())
