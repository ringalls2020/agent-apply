from datetime import datetime, timedelta
from collections.abc import Iterator

import pytest

from backend.db import create_db_engine, create_session_factory
from backend.db_models import Base
from backend.models import AgentRunRequest, CandidateProfile
from backend.services import OpportunityAgent, PostgresStore


@pytest.fixture
def store() -> Iterator[PostgresStore]:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    pg_store = PostgresStore(session_factory=create_session_factory(engine))
    yield pg_store
    engine.dispose()


def build_request(max_opportunities: int = 3) -> AgentRunRequest:
    return AgentRunRequest(
        profile=CandidateProfile(
            full_name="Jane Doe",
            email="jane@example.com",
            resume_text="Built ML products and automation pipelines.",
            interests=["ai", "climate", "robotics"],
        ),
        max_opportunities=max_opportunities,
    )


def test_postgres_store_returns_records_sorted_by_discovered_date_desc(
    store: PostgresStore,
) -> None:
    agent = OpportunityAgent(store=store)
    records = agent.run(user_id="user-1", request=build_request(max_opportunities=2))

    records[0].opportunity.discovered_at = datetime.utcnow() - timedelta(days=2)
    records[1].opportunity.discovered_at = datetime.utcnow() - timedelta(days=1)

    store.upsert_for_user("user-1", records[0])
    store.upsert_for_user("user-1", records[1])

    sorted_records = store.list_for_user("user-1")

    assert len(sorted_records) == 2
    assert sorted_records[0].opportunity.discovered_at > sorted_records[1].opportunity.discovered_at


def test_opportunity_agent_run_executes_full_pipeline(store: PostgresStore) -> None:
    agent = OpportunityAgent(store=store)

    records = agent.run(user_id="user-1", request=build_request(max_opportunities=4))

    assert len(records) == 4
    assert len(store.list_for_user("user-1")) == 4

    for record in records:
        assert record.submitted_at is not None
        assert record.notified_at is not None
        assert record.contact is not None
        assert record.contact.email.startswith("recruiting@")
        assert record.status.value == "notified"
        assert record.opportunity.url.startswith("https://")


def test_discovery_reuses_interest_keywords_across_generated_roles(
    store: PostgresStore,
) -> None:
    agent = OpportunityAgent(store=store)

    opportunities = agent._discover(build_request(max_opportunities=5))

    assert len(opportunities) == 5
    expected_titles = [
        "Ai Fellow",
        "Climate Fellow",
        "Robotics Fellow",
        "Ai Fellow",
        "Climate Fellow",
    ]
    assert [item.title for item in opportunities] == expected_titles
