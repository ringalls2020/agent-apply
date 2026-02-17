from __future__ import annotations

from datetime import datetime
from typing import Dict, List
from uuid import uuid4

from .models import (
    AgentRunRequest,
    ApplicationRecord,
    ApplicationStatus,
    Contact,
    Opportunity,
)


class InMemoryStore:
    def __init__(self) -> None:
        self.applications: Dict[str, ApplicationRecord] = {}

    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        self.applications[record.id] = record
        return record

    def list_all(self) -> List[ApplicationRecord]:
        return sorted(
            self.applications.values(),
            key=lambda item: item.opportunity.discovered_at,
            reverse=True,
        )


class OpportunityAgent:
    """Simple deterministic mock agent for discovery + apply pipeline.

    Replace the mock methods with real providers for:
      - internet search (SerpAPI, Tavily, Bing, etc.)
      - automated applications (Playwright/RPA with guardrails)
      - contact enrichment (Apollo/Clearbit/LinkedIn API)
      - notifications (email/slack/sms)
    """

    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    def run(self, request: AgentRunRequest) -> List[ApplicationRecord]:
        opportunities = self._discover(request)
        records: List[ApplicationRecord] = []

        for opp in opportunities:
            applied_record = self._apply(opp)
            enriched_record = self._find_point_of_contact(applied_record)
            notified_record = self._notify(enriched_record)
            records.append(self.store.upsert(notified_record))

        return records

    def _discover(self, request: AgentRunRequest) -> List[Opportunity]:
        interests = ", ".join(request.profile.interests)
        opportunities = []

        for idx in range(request.max_opportunities):
            opportunities.append(
                Opportunity(
                    id=str(uuid4()),
                    title=f"{request.profile.interests[idx % len(request.profile.interests)].title()} Fellow",
                    company=f"Novel Labs {idx + 1}",
                    url=f"https://example.com/jobs/{idx + 1}",
                    reason=(
                        f"Matched resume with interests ({interests}) and found a novel role "
                        "with high skills overlap."
                    ),
                )
            )

        return opportunities

    def _apply(self, opportunity: Opportunity) -> ApplicationRecord:
        return ApplicationRecord(
            id=str(uuid4()),
            opportunity=opportunity,
            status=ApplicationStatus.applied,
            submitted_at=datetime.utcnow(),
        )

    def _find_point_of_contact(self, record: ApplicationRecord) -> ApplicationRecord:
        contact = Contact(
            name=f"Recruiter for {record.opportunity.company}",
            email=f"recruiting@{record.opportunity.company.lower().replace(' ', '')}.com",
            role="Talent Acquisition",
            source="Company careers page",
        )
        record.contact = contact
        return record

    def _notify(self, record: ApplicationRecord) -> ApplicationRecord:
        record.status = ApplicationStatus.notified
        record.notified_at = datetime.utcnow()
        return record
