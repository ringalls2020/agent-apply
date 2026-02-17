from collections.abc import Iterator
from datetime import timedelta

import pytest

from common.time import utc_now

from backend.db import create_db_engine, create_session_factory
from backend.db_models import Base
from backend.models import ApplicationRecord, ApplicationStatus, Opportunity
from backend.services import PostgresStore


@pytest.fixture
def store() -> Iterator[PostgresStore]:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    pg_store = PostgresStore(session_factory=create_session_factory(engine))
    yield pg_store
    engine.dispose()


def _build_record(*, app_id: str, opportunity_id: str, discovered_at_offset_days: int) -> ApplicationRecord:
    return ApplicationRecord(
        id=app_id,
        opportunity=Opportunity(
            id=opportunity_id,
            title="Backend Engineer",
            company="Acme",
            url=f"https://example.com/jobs/{opportunity_id}",
            reason="Strong backend overlap",
            discovered_at=utc_now() - timedelta(days=discovered_at_offset_days),
        ),
        status=ApplicationStatus.review,
    )


def test_postgres_store_returns_records_sorted_by_discovered_date_desc(
    store: PostgresStore,
) -> None:
    older = _build_record(app_id="app-1", opportunity_id="job-1", discovered_at_offset_days=2)
    newer = _build_record(app_id="app-2", opportunity_id="job-2", discovered_at_offset_days=1)

    store.upsert_for_user("user-1", older)
    store.upsert_for_user("user-1", newer)

    sorted_records = store.list_for_user("user-1")

    assert len(sorted_records) == 2
    assert sorted_records[0].id == "app-2"
    assert sorted_records[1].id == "app-1"


def test_mark_viewed_and_mark_applied_transitions(store: PostgresStore) -> None:
    record = _build_record(app_id="app-1", opportunity_id="job-1", discovered_at_offset_days=0)
    store.upsert_for_user("user-1", record)

    viewed = store.mark_viewed_for_user_application(user_id="user-1", application_id="app-1")
    assert viewed is not None
    assert viewed.status == ApplicationStatus.viewed

    submitted_at = utc_now()
    applied = store.mark_applied_for_user_application(
        user_id="user-1",
        application_id="app-1",
        submitted_at=submitted_at,
    )
    assert applied is not None
    assert applied.status == ApplicationStatus.applied
    assert applied.submitted_at == submitted_at


def test_search_for_user_filters_by_status_and_company(store: PostgresStore) -> None:
    first = _build_record(app_id="app-1", opportunity_id="job-1", discovered_at_offset_days=0)
    second = _build_record(app_id="app-2", opportunity_id="job-2", discovered_at_offset_days=0)
    second.opportunity.company = "Globex"
    second.status = ApplicationStatus.applied

    store.upsert_for_user("user-1", first)
    store.upsert_for_user("user-1", second)

    apps, total = store.search_for_user(
        user_id="user-1",
        statuses=[ApplicationStatus.applied],
        companies=["Globex"],
        limit=10,
        offset=0,
    )

    assert total == 1
    assert len(apps) == 1
    assert apps[0].id == "app-2"
