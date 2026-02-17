from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    applied = "applied"
    contacted = "contacted"
    notified = "notified"
    archived = "archived"


class Opportunity(BaseModel):
    id: str
    title: str
    company: str
    location: str
    employment_type: str = "full-time"
    url: HttpUrl
    reason: str
    discovered_at: datetime = Field(default_factory=datetime.utcnow)


class Contact(BaseModel):
    name: str
    email: EmailStr
    role: Optional[str] = None
    source: str


class ApplicationRecord(BaseModel):
    id: str
    opportunity: Opportunity
    status: ApplicationStatus = ApplicationStatus.discovered
    contact: Optional[Contact] = None
    notes: Optional[str] = None
    submitted_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CandidateProfile(BaseModel):
    full_name: str
    email: EmailStr
    resume_text: str = Field(min_length=50)
    interests: List[str] = Field(min_length=1)
    locations: List[str] = Field(default_factory=lambda: ["Remote"])


class AgentRunRequest(BaseModel):
    profile: CandidateProfile
    max_opportunities: int = Field(default=5, ge=1, le=25)
    auto_apply: bool = True


class AgentRunResponse(BaseModel):
    applications: List[ApplicationRecord]


class UpdateApplicationStatusRequest(BaseModel):
    status: ApplicationStatus
    notes: Optional[str] = None
