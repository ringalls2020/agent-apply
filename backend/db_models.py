from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ApplicationRecordRow(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    opportunity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    opportunity_title: Mapped[str] = mapped_column(String(255), nullable=False)
    opportunity_company: Mapped[str] = mapped_column(String(255), nullable=False)
    opportunity_url: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity_reason: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity_discovered_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )

    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    contact_role: Mapped[str | None] = mapped_column(String(255))
    contact_source: Mapped[str | None] = mapped_column(String(255))

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
