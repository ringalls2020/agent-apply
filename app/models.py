from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ApplicationStatus(str, Enum):
    discovered = "discovered"
    applied = "applied"
    notified = "notified"


class Opportunity(BaseModel):
    id: str
    title: str
    company: str
    url: str
    reason: str
    discovered_at: datetime = Field(default_factory=datetime.utcnow)


class Contact(BaseModel):
    name: str
    email: str
    role: Optional[str] = None
    source: str


class ApplicationRecord(BaseModel):
    id: str
    opportunity: Opportunity
    status: ApplicationStatus = ApplicationStatus.discovered
    contact: Optional[Contact] = None
    submitted_at: Optional[datetime] = None
    notified_at: Optional[datetime] = None


class CandidateProfile(BaseModel):
    full_name: str
    email: str
    resume_text: str
    interests: List[str] = Field(min_length=1)


class AgentRunRequest(BaseModel):
    profile: CandidateProfile
    max_opportunities: int = Field(default=5, ge=1, le=25)


class AgentRunResponse(BaseModel):
    applications: List[ApplicationRecord]
