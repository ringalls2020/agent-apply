from __future__ import annotations

import logging
import os
import uuid

from sqlalchemy import select

from common.time import utc_now

from cloud_automation.db import (
    create_db_engine,
    create_session_factory,
    ensure_runtime_indexes,
    get_database_url,
)
from cloud_automation.db_models import (
    ApplyAttemptRow,
    JobFingerprintRow,
    JobIdentityRow,
    MatchResultRow,
    NormalizedJobRow,
)
from cloud_automation.logging_config import configure_logging
from cloud_automation.services.ats_token_utils import build_job_identity

logger = logging.getLogger(__name__)


def _merge_duplicate_job(*, session, duplicate_job_id: str, canonical_job_id: str) -> None:
    if duplicate_job_id == canonical_job_id:
        return

    fingerprint_rows = session.scalars(
        select(JobFingerprintRow).where(JobFingerprintRow.canonical_job_id == duplicate_job_id)
    ).all()
    for row in fingerprint_rows:
        row.canonical_job_id = canonical_job_id

    identity_rows = session.scalars(
        select(JobIdentityRow).where(JobIdentityRow.canonical_job_id == duplicate_job_id)
    ).all()
    for row in identity_rows:
        row.canonical_job_id = canonical_job_id
        row.last_seen_at = utc_now()

    match_rows = session.scalars(
        select(MatchResultRow).where(MatchResultRow.external_job_id == duplicate_job_id)
    ).all()
    for row in match_rows:
        row.external_job_id = canonical_job_id

    attempt_rows = session.scalars(
        select(ApplyAttemptRow).where(ApplyAttemptRow.external_job_id == duplicate_job_id)
    ).all()
    for row in attempt_rows:
        row.external_job_id = canonical_job_id

    duplicate_job = session.get(NormalizedJobRow, duplicate_job_id)
    if duplicate_job is not None:
        session.delete(duplicate_job)


def run() -> None:
    configure_logging()
    engine = create_db_engine(get_database_url())
    ensure_runtime_indexes(engine)
    session_factory = create_session_factory(engine)

    created_identities = 0
    merged_duplicates = 0
    with session_factory() as session:
        existing_identity_rows = session.scalars(select(JobIdentityRow)).all()
        identity_by_key = {row.canonical_key: row for row in existing_identity_rows}

        jobs = session.scalars(
            select(NormalizedJobRow).order_by(NormalizedJobRow.created_at.asc())
        ).all()
        for row in jobs:
            current = session.get(NormalizedJobRow, row.id)
            if current is None:
                continue

            identity = build_job_identity(
                source=current.source,
                apply_url=current.apply_url,
                external_job_id=current.id,
            )
            existing_identity = identity_by_key.get(identity.canonical_key)
            if existing_identity is None:
                identity_row = JobIdentityRow(
                    id=str(uuid.uuid4()),
                    canonical_key=identity.canonical_key,
                    canonical_job_id=current.id,
                    provider=identity.provider,
                    provider_token=identity.provider_token,
                    provider_job_id=identity.provider_job_id,
                    normalized_apply_url_hash=identity.normalized_apply_url_hash,
                    first_seen_at=utc_now(),
                    last_seen_at=utc_now(),
                )
                session.add(identity_row)
                identity_by_key[identity.canonical_key] = identity_row
                created_identities += 1
                continue

            if session.get(NormalizedJobRow, existing_identity.canonical_job_id) is None:
                existing_identity.canonical_job_id = current.id

            if existing_identity.canonical_job_id != current.id:
                _merge_duplicate_job(
                    session=session,
                    duplicate_job_id=current.id,
                    canonical_job_id=existing_identity.canonical_job_id,
                )
                merged_duplicates += 1
                continue

            existing_identity.provider = identity.provider
            existing_identity.provider_token = identity.provider_token
            existing_identity.provider_job_id = identity.provider_job_id
            existing_identity.normalized_apply_url_hash = identity.normalized_apply_url_hash
            existing_identity.last_seen_at = utc_now()

        session.commit()

    logger.info(
        "job_dedupe_backfill_completed",
        extra={
            "created_identities": created_identities,
            "merged_duplicates": merged_duplicates,
        },
    )


if __name__ == "__main__":
    run()
