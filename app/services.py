from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List
from uuid import uuid4

from .models import (
    AgentRunRequest,
    ApplicationRecord,
    ApplicationStatus,
    Contact,
    Opportunity,
)
from .store import InMemoryStore


@dataclass
class DiscoveryService:
    def find_opportunities(self, request: AgentRunRequest) -> List[Opportunity]:
        interests = ", ".join(request.profile.interests)
        opportunities: List[Opportunity] = []

        for idx in range(request.max_opportunities):
            topic = request.profile.interests[idx % len(request.profile.interests)].title()
            location = request.profile.locations[idx % len(request.profile.locations)]
            opportunities.append(
                Opportunity(
                    id=str(uuid4()),
                    title=f"{topic} Fellow",
                    company=f"Novel Labs {idx + 1}",
                    location=location,
                    employment_type="full-time",
                    url=f"https://example.com/jobs/{idx + 1}",
                    reason=(
                        f"Matched resume against interests ({interests}) and selected a role "
                        "with strong overlap in skills and stated focus areas."
                    ),
                )
            )

        return opportunities


@dataclass
class ApplyService:
    def submit(self, opportunity: Opportunity) -> ApplicationRecord:
        return ApplicationRecord(
            id=str(uuid4()),
            opportunity=opportunity,
            status=ApplicationStatus.applied,
            submitted_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )


@dataclass
class ContactService:
    def enrich(self, record: ApplicationRecord) -> ApplicationRecord:
        record.contact = Contact(
            name=f"Recruiter for {record.opportunity.company}",
            email=f"recruiting@{record.opportunity.company.lower().replace(' ', '')}.com",
            role="Talent Acquisition",
            source="Company careers page",
        )
        record.status = ApplicationStatus.contacted
        record.updated_at = datetime.utcnow()
        return record


@dataclass
class NotificationService:
    def notify(self, record: ApplicationRecord) -> ApplicationRecord:
        record.status = ApplicationStatus.notified
        record.notified_at = datetime.utcnow()
        record.updated_at = datetime.utcnow()
        return record


class OpportunityAgent:
    """Coordinator for discover -> apply -> contact -> notify."""

    def __init__(
        self,
        store: InMemoryStore,
        discovery: DiscoveryService | None = None,
        applier: ApplyService | None = None,
        contacts: ContactService | None = None,
        notifier: NotificationService | None = None,
    ) -> None:
        self.store = store
        self.discovery = discovery or DiscoveryService()
        self.applier = applier or ApplyService()
        self.contacts = contacts or ContactService()
        self.notifier = notifier or NotificationService()

    def run(self, request: AgentRunRequest) -> List[ApplicationRecord]:
        opportunities = self.discovery.find_opportunities(request)
        records: List[ApplicationRecord] = []

        for opp in opportunities:
            if request.auto_apply:
                record = self.applier.submit(opp)
                record = self.contacts.enrich(record)
                record = self.notifier.notify(record)
            else:
                record = ApplicationRecord(
                    id=str(uuid4()),
                    opportunity=opp,
                    status=ApplicationStatus.discovered,
                    updated_at=datetime.utcnow(),
                )

            records.append(self.store.upsert(record))

        return records
