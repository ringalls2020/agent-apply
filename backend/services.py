from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db_models import ApplicationRecordRow
from .models import (
    AgentRunRequest,
    ApplicationRecord,
    ApplicationStatus,
    Contact,
    Opportunity,
)


class ApplicationStore(Protocol):
    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        ...

    def list_all(self) -> List[ApplicationRecord]:
        ...


class PostgresStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        with self._session_factory() as session:
            row = session.get(ApplicationRecordRow, record.id)

            if row is None:
                row = ApplicationRecordRow(id=record.id, status=record.status.value)
                session.add(row)

            self._sync_row(row=row, record=record)
            session.commit()
            session.refresh(row)

            return self._to_record(row)

    def list_all(self) -> List[ApplicationRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ApplicationRecordRow).order_by(
                    ApplicationRecordRow.opportunity_discovered_at.desc()
                )
            ).all()
            return [self._to_record(row) for row in rows]

    @staticmethod
    def _sync_row(*, row: ApplicationRecordRow, record: ApplicationRecord) -> None:
        row.status = record.status.value
        row.opportunity_id = record.opportunity.id
        row.opportunity_title = record.opportunity.title
        row.opportunity_company = record.opportunity.company
        row.opportunity_url = record.opportunity.url
        row.opportunity_reason = record.opportunity.reason
        row.opportunity_discovered_at = record.opportunity.discovered_at
        row.submitted_at = record.submitted_at
        row.notified_at = record.notified_at

        if record.contact is None:
            row.contact_name = None
            row.contact_email = None
            row.contact_role = None
            row.contact_source = None
            return

        row.contact_name = record.contact.name
        row.contact_email = record.contact.email
        row.contact_role = record.contact.role
        row.contact_source = record.contact.source

    @staticmethod
    def _to_record(row: ApplicationRecordRow) -> ApplicationRecord:
        contact = None

        if (
            row.contact_name is not None
            and row.contact_email is not None
            and row.contact_source is not None
        ):
            contact = Contact(
                name=row.contact_name,
                email=row.contact_email,
                role=row.contact_role,
                source=row.contact_source,
            )

        return ApplicationRecord(
            id=row.id,
            opportunity=Opportunity(
                id=row.opportunity_id,
                title=row.opportunity_title,
                company=row.opportunity_company,
                url=row.opportunity_url,
                reason=row.opportunity_reason,
                discovered_at=row.opportunity_discovered_at,
            ),
            status=ApplicationStatus(row.status),
            contact=contact,
            submitted_at=row.submitted_at,
            notified_at=row.notified_at,
        )


class OpportunityAgent:
    """Simple deterministic mock agent for discovery + apply pipeline.

    Replace the mock methods with real providers for:
      - internet search (SerpAPI, Tavily, Bing, etc.)
      - automated applications (Playwright/RPA with guardrails)
      - contact enrichment (Apollo/Clearbit/LinkedIn API)
      - notifications (email/slack/sms)
    """

    def __init__(self, store: ApplicationStore) -> None:
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
