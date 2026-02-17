from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


class ApplicationStore(Protocol):
    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        ...

    def list_all(self) -> List[ApplicationRecord]:
        ...


class PostgresStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        logger.debug("postgres_store_initialized")

    def upsert(self, record: ApplicationRecord) -> ApplicationRecord:
        logger.debug(
            "store_upsert_started",
            extra={"record_id": record.id, "status": record.status.value},
        )
        try:
            with self._session_factory() as session:
                row = session.get(ApplicationRecordRow, record.id)

                if row is None:
                    row = ApplicationRecordRow(id=record.id, status=record.status.value)
                    session.add(row)

                self._sync_row(row=row, record=record)
                session.commit()
                session.refresh(row)

                stored = self._to_record(row)
        except Exception:
            logger.exception("store_upsert_failed", extra={"record_id": record.id})
            raise

        logger.debug(
            "store_upsert_completed",
            extra={"record_id": stored.id, "status": stored.status.value},
        )
        return stored

    def list_all(self) -> List[ApplicationRecord]:
        logger.debug("store_list_all_started")
        try:
            with self._session_factory() as session:
                rows = session.scalars(
                    select(ApplicationRecordRow).order_by(
                        ApplicationRecordRow.opportunity_discovered_at.desc()
                    )
                ).all()
                records = [self._to_record(row) for row in rows]
        except Exception:
            logger.exception("store_list_all_failed")
            raise

        logger.debug("store_list_all_completed", extra={"records": len(records)})
        return records

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
        logger.debug("opportunity_agent_initialized")

    def run(self, request: AgentRunRequest) -> List[ApplicationRecord]:
        logger.info(
            "agent_run_started",
            extra={
                "max_opportunities": request.max_opportunities,
                "interest_count": len(request.profile.interests),
            },
        )
        opportunities = self._discover(request)
        records: List[ApplicationRecord] = []

        for idx, opp in enumerate(opportunities, start=1):
            logger.debug(
                "agent_processing_opportunity",
                extra={
                    "step_index": idx,
                    "opportunity_id": opp.id,
                    "company": opp.company,
                },
            )
            applied_record = self._apply(opp)
            enriched_record = self._find_point_of_contact(applied_record)
            notified_record = self._notify(enriched_record)
            records.append(self.store.upsert(notified_record))

        logger.info("agent_run_completed", extra={"generated_records": len(records)})
        return records

    def _discover(self, request: AgentRunRequest) -> List[Opportunity]:
        logger.debug(
            "agent_discover_started",
            extra={"max_opportunities": request.max_opportunities},
        )
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

        logger.debug(
            "agent_discover_completed", extra={"discovered_count": len(opportunities)}
        )
        return opportunities

    def _apply(self, opportunity: Opportunity) -> ApplicationRecord:
        record = ApplicationRecord(
            id=str(uuid4()),
            opportunity=opportunity,
            status=ApplicationStatus.applied,
            submitted_at=datetime.utcnow(),
        )
        logger.debug(
            "agent_apply_completed",
            extra={
                "application_id": record.id,
                "opportunity_id": opportunity.id,
            },
        )
        return record

    def _find_point_of_contact(self, record: ApplicationRecord) -> ApplicationRecord:
        contact = Contact(
            name=f"Recruiter for {record.opportunity.company}",
            email=f"recruiting@{record.opportunity.company.lower().replace(' ', '')}.com",
            role="Talent Acquisition",
            source="Company careers page",
        )
        record.contact = contact
        logger.debug(
            "agent_contact_enriched",
            extra={
                "application_id": record.id,
                "contact_source": contact.source,
            },
        )
        return record

    def _notify(self, record: ApplicationRecord) -> ApplicationRecord:
        record.status = ApplicationStatus.notified
        record.notified_at = datetime.utcnow()
        logger.debug("agent_notify_completed", extra={"application_id": record.id})
        return record
