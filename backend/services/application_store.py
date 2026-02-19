from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, List

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from common.time import utc_now

from ..db_models import ApplicationRecordRow
from ..models import (
    ApplicationRecord,
    ApplicationStatus,
    Contact,
    Opportunity,
)

logger = logging.getLogger(__name__)


class PostgresStore:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        job_listing_ttl_days: int = 21,
    ) -> None:
        self._session_factory = session_factory
        self._job_listing_ttl_days = max(int(job_listing_ttl_days), 1)
        logger.debug("postgres_store_initialized")

    def upsert_for_user(
        self, user_id: str, record: ApplicationRecord
    ) -> ApplicationRecord:
        logger.debug(
            "store_upsert_started",
            extra={"record_id": record.id, "status": record.status.value, "user_id": user_id},
        )
        try:
            with self._session_factory() as session:
                row = session.get(ApplicationRecordRow, record.id)

                if row is None:
                    row = ApplicationRecordRow(id=record.id, status=record.status.value)
                    session.add(row)

                self._sync_row(row=row, record=record, user_id=user_id)
                session.commit()
                session.refresh(row)

                stored = self._to_record(row)
        except Exception:
            logger.exception("store_upsert_failed", extra={"record_id": record.id})
            raise

        logger.debug(
            "store_upsert_completed",
            extra={"record_id": stored.id, "status": stored.status.value, "user_id": user_id},
        )
        return stored

    def upsert_many_for_user(
        self,
        user_id: str,
        records: list[ApplicationRecord],
    ) -> list[ApplicationRecord]:
        if not records:
            return []

        with self._session_factory() as session:
            ids = [record.id for record in records]
            existing_rows = session.scalars(
                select(ApplicationRecordRow).where(ApplicationRecordRow.id.in_(ids))
            ).all()
            row_by_id: dict[str, ApplicationRecordRow] = {
                row.id: row for row in existing_rows
            }

            for record in records:
                row = row_by_id.get(record.id)
                if row is None:
                    row = ApplicationRecordRow(id=record.id, status=record.status.value)
                    session.add(row)
                    row_by_id[record.id] = row
                self._sync_row(row=row, record=record, user_id=user_id)

            session.commit()
            return [self._to_record(row_by_id[record.id]) for record in records]

    def _archive_cutoff(self) -> datetime:
        return utc_now() - timedelta(days=self._job_listing_ttl_days)

    def _is_archived_at(self, discovered_at: datetime) -> bool:
        return discovered_at < self._archive_cutoff()

    def list_for_user(self, user_id: str, *, include_archived: bool = False) -> List[ApplicationRecord]:
        logger.debug("store_list_for_user_started", extra={"user_id": user_id})
        try:
            with self._session_factory() as session:
                stmt = (
                    select(ApplicationRecordRow)
                    .where(ApplicationRecordRow.user_id == user_id)
                    .order_by(ApplicationRecordRow.opportunity_discovered_at.desc())
                )
                if not include_archived:
                    stmt = stmt.where(
                        ApplicationRecordRow.opportunity_discovered_at >= self._archive_cutoff()
                    )
                rows = session.scalars(stmt).all()
                records = [self._to_record(row) for row in rows]
        except Exception:
            logger.exception("store_list_for_user_failed", extra={"user_id": user_id})
            raise

        logger.debug(
            "store_list_for_user_completed",
            extra={"records": len(records), "user_id": user_id},
        )
        return records

    @classmethod
    def _source_clause(cls, source: str):
        normalized_source = source.strip().lower()
        lower_url = func.lower(func.coalesce(ApplicationRecordRow.opportunity_url, ""))
        if normalized_source == "greenhouse":
            return lower_url.like("%greenhouse%")
        if normalized_source == "lever":
            return lower_url.like("%lever.co%")
        if normalized_source == "smartrecruiters":
            return lower_url.like("%smartrecruiters%")
        if normalized_source == "workday":
            return or_(
                lower_url.like("%myworkdayjobs.com%"),
                lower_url.like("%workday%"),
            )
        if normalized_source == "other":
            return and_(
                not_(lower_url.like("%greenhouse%")),
                not_(lower_url.like("%lever.co%")),
                not_(lower_url.like("%smartrecruiters%")),
                not_(lower_url.like("%myworkdayjobs.com%")),
                not_(lower_url.like("%workday%")),
            )
        return None

    def search_for_user(
        self,
        *,
        user_id: str,
        statuses: list[ApplicationStatus] | None = None,
        q: str | None = None,
        companies: list[str] | None = None,
        sources: list[str] | None = None,
        has_contact: bool | None = None,
        discovered_from: datetime | None = None,
        discovered_to: datetime | None = None,
        sort_by: str = "discovered_at",
        sort_dir: str = "desc",
        limit: int = 25,
        offset: int = 0,
        include_archived: bool = False,
    ) -> tuple[list[ApplicationRecord], int]:
        normalized_limit = min(max(limit, 1), 100)
        normalized_offset = max(offset, 0)

        filters = [ApplicationRecordRow.user_id == user_id]
        if not include_archived:
            filters.append(ApplicationRecordRow.opportunity_discovered_at >= self._archive_cutoff())
        if statuses:
            filters.append(ApplicationRecordRow.status.in_([status.value for status in statuses]))

        normalized_query = q.strip().lower() if isinstance(q, str) else ""
        if normalized_query:
            pattern = f"%{normalized_query}%"
            filters.append(
                or_(
                    func.lower(func.coalesce(ApplicationRecordRow.opportunity_title, "")).like(pattern),
                    func.lower(func.coalesce(ApplicationRecordRow.opportunity_company, "")).like(pattern),
                    func.lower(func.coalesce(ApplicationRecordRow.contact_name, "")).like(pattern),
                    func.lower(func.coalesce(ApplicationRecordRow.contact_email, "")).like(pattern),
                )
            )

        normalized_companies = [
            company.strip().lower()
            for company in (companies or [])
            if isinstance(company, str) and company.strip()
        ]
        if normalized_companies:
            filters.append(
                func.lower(ApplicationRecordRow.opportunity_company).in_(normalized_companies)
            )

        source_clauses = []
        for source in (sources or []):
            if not isinstance(source, str):
                continue
            clause = self._source_clause(source)
            if clause is not None:
                source_clauses.append(clause)
        if source_clauses:
            filters.append(or_(*source_clauses))

        if has_contact is True:
            filters.append(
                or_(
                    ApplicationRecordRow.contact_name.is_not(None),
                    ApplicationRecordRow.contact_email.is_not(None),
                )
            )
        elif has_contact is False:
            filters.append(
                and_(
                    ApplicationRecordRow.contact_name.is_(None),
                    ApplicationRecordRow.contact_email.is_(None),
                )
            )

        if discovered_from is not None:
            filters.append(ApplicationRecordRow.opportunity_discovered_at >= discovered_from)
        if discovered_to is not None:
            filters.append(ApplicationRecordRow.opportunity_discovered_at <= discovered_to)

        if sort_by == "company":
            sort_column = func.lower(ApplicationRecordRow.opportunity_company)
        elif sort_by == "status":
            sort_column = func.lower(ApplicationRecordRow.status)
        else:
            sort_column = ApplicationRecordRow.opportunity_discovered_at

        order_by = (
            sort_column.asc()
            if sort_dir.strip().lower() == "asc"
            else sort_column.desc()
        )

        with self._session_factory() as session:
            count_stmt = (
                select(func.count())
                .select_from(ApplicationRecordRow)
                .where(and_(*filters))
            )
            total_count = int(session.scalar(count_stmt) or 0)

            rows = session.scalars(
                select(ApplicationRecordRow)
                .where(and_(*filters))
                .order_by(order_by, ApplicationRecordRow.id.asc())
                .offset(normalized_offset)
                .limit(normalized_limit)
            ).all()
            return [self._to_record(row) for row in rows], total_count

    def get_for_user_by_ids(
        self,
        *,
        user_id: str,
        application_ids: list[str],
        include_archived: bool = False,
    ) -> list[ApplicationRecord]:
        if not application_ids:
            return []
        with self._session_factory() as session:
            stmt = select(ApplicationRecordRow).where(
                and_(
                    ApplicationRecordRow.user_id == user_id,
                    ApplicationRecordRow.id.in_(application_ids),
                )
            )
            if not include_archived:
                stmt = stmt.where(
                    ApplicationRecordRow.opportunity_discovered_at >= self._archive_cutoff()
                )
            rows = session.scalars(stmt).all()
            row_by_id = {row.id: row for row in rows}
            return [
                self._to_record(row_by_id[application_id])
                for application_id in application_ids
                if application_id in row_by_id
            ]

    def get_for_user_by_opportunity_ids(
        self,
        *,
        user_id: str,
        opportunity_ids: list[str],
        include_archived: bool = False,
    ) -> list[ApplicationRecord]:
        if not opportunity_ids:
            return []

        with self._session_factory() as session:
            stmt = select(ApplicationRecordRow).where(
                and_(
                    ApplicationRecordRow.user_id == user_id,
                    ApplicationRecordRow.opportunity_id.in_(opportunity_ids),
                )
            )
            if not include_archived:
                stmt = stmt.where(
                    ApplicationRecordRow.opportunity_discovered_at >= self._archive_cutoff()
                )
            rows = session.scalars(stmt).all()
            return [self._to_record(row) for row in rows]

    def mark_viewed_for_user_application(
        self, *, user_id: str, application_id: str
    ) -> ApplicationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRecordRow)
                .where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id == application_id,
                    )
                )
                .limit(1)
            )
            if row is None:
                return None

            current_status = row.status.strip().lower()
            if current_status == ApplicationStatus.review.value:
                row.status = ApplicationStatus.viewed.value
                session.commit()
                session.refresh(row)
            return self._to_record(row)

    def mark_applied_for_user_application(
        self,
        *,
        user_id: str,
        application_id: str,
        submitted_at: datetime,
    ) -> ApplicationRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRecordRow)
                .where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id == application_id,
                    )
                )
                .limit(1)
            )
            if row is None:
                return None

            row.status = ApplicationStatus.applied.value
            row.submitted_at = submitted_at
            session.commit()
            session.refresh(row)
            return self._to_record(row)

    def update_status_for_user_application_ids(
        self,
        *,
        user_id: str,
        application_ids: list[str],
        status: ApplicationStatus,
    ) -> list[ApplicationRecord]:
        if not application_ids:
            return []

        with self._session_factory() as session:
            rows = session.scalars(
                select(ApplicationRecordRow).where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.id.in_(application_ids),
                    )
                )
            ).all()
            row_by_id = {row.id: row for row in rows}
            for application_id in application_ids:
                row = row_by_id.get(application_id)
                if row is None:
                    continue
                row.status = status.value

            session.commit()
            for row in rows:
                session.refresh(row)

            return [
                self._to_record(row_by_id[application_id])
                for application_id in application_ids
                if application_id in row_by_id
            ]

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

    def update_status_for_user_opportunity(
        self,
        *,
        user_id: str,
        opportunity_id: str,
        status: ApplicationStatus,
        submitted_at: datetime | None = None,
    ) -> None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ApplicationRecordRow)
                .where(
                    and_(
                        ApplicationRecordRow.user_id == user_id,
                        ApplicationRecordRow.opportunity_id == opportunity_id,
                    )
                )
                .limit(1)
            )
            if row is None:
                return

            row.status = status.value
            if submitted_at is not None:
                row.submitted_at = submitted_at
            session.commit()

    @staticmethod
    def _sync_row(*, row: ApplicationRecordRow, record: ApplicationRecord, user_id: str) -> None:
        row.user_id = user_id
        if row.status:
            try:
                existing_status = ApplicationStatus(row.status)
            except Exception:
                existing_status = None
            else:
                if (
                    existing_status in {ApplicationStatus.applied, ApplicationStatus.notified, ApplicationStatus.failed}
                    and record.status in {ApplicationStatus.review, ApplicationStatus.viewed, ApplicationStatus.applying}
                ):
                    row.status = existing_status.value
                else:
                    row.status = record.status.value
        else:
            row.status = record.status.value
        row.opportunity_id = record.opportunity.id
        row.opportunity_title = record.opportunity.title
        row.opportunity_company = record.opportunity.company
        row.opportunity_url = record.opportunity.url
        row.opportunity_reason = record.opportunity.reason
        row.opportunity_discovered_at = record.opportunity.discovered_at
        if record.submitted_at is not None:
            row.submitted_at = record.submitted_at
        if record.notified_at is not None:
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

    def _to_record(self, row: ApplicationRecordRow) -> ApplicationRecord:
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
            is_archived=self._is_archived_at(row.opportunity_discovered_at),
            contact=contact,
            submitted_at=row.submitted_at,
            notified_at=row.notified_at,
        )

__all__ = ["PostgresStore"]
