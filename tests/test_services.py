from datetime import datetime, timedelta

from backend.models import AgentRunRequest, CandidateProfile
from backend.services import InMemoryStore, OpportunityAgent


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


def test_in_memory_store_returns_records_sorted_by_discovered_date_desc() -> None:
    store = InMemoryStore()
    agent = OpportunityAgent(store=store)
    records = agent.run(build_request(max_opportunities=2))

    records[0].opportunity.discovered_at = datetime.utcnow() - timedelta(days=2)
    records[1].opportunity.discovered_at = datetime.utcnow() - timedelta(days=1)

    store.upsert(records[0])
    store.upsert(records[1])

    sorted_records = store.list_all()

    assert len(sorted_records) == 2
    assert sorted_records[0].opportunity.discovered_at > sorted_records[1].opportunity.discovered_at


def test_opportunity_agent_run_executes_full_pipeline() -> None:
    store = InMemoryStore()
    agent = OpportunityAgent(store=store)

    records = agent.run(build_request(max_opportunities=4))

    assert len(records) == 4
    assert len(store.list_all()) == 4

    for record in records:
        assert record.submitted_at is not None
        assert record.notified_at is not None
        assert record.contact is not None
        assert record.contact.email.startswith("recruiting@")
        assert record.status.value == "notified"
        assert record.opportunity.url.startswith("https://")


def test_discovery_reuses_interest_keywords_across_generated_roles() -> None:
    store = InMemoryStore()
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
