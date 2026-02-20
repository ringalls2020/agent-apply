from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.models import (
    ApplicationProfileUpsertRequest,
    ResumeUpsertRequest,
    UserUpsertRequest,
)
from backend.services.resume_utils import extract_resume_profile_facts


def test_extract_resume_profile_facts_deterministic() -> None:
    resume_text = """
    Senior Staff Software Engineer at ResumeCorp
    Location: Austin, Texas, United States
    Built cloud automation systems and reliability tooling.
    """
    extracted = extract_resume_profile_facts(resume_text)
    assert extracted.current_title == "Senior Staff Software Engineer"
    assert extracted.current_company == "ResumeCorp"
    assert extracted.most_recent_company == "ResumeCorp"
    assert extracted.target_work_city == "Austin"
    assert extracted.target_work_state == "Texas"
    assert extracted.target_work_country == "United States"


def test_resume_profile_extraction_respects_manual_values() -> None:
    app = create_app(database_url="sqlite+pysqlite:///:memory:")
    with TestClient(app):
        store = app.state.main_store
        user_id = "user-resume-profile"
        store.upsert_user(
            user_id=user_id,
            payload=UserUpsertRequest(full_name="Resume Profile", email="resume-profile@example.com"),
        )
        store.upsert_application_profile(
            user_id=user_id,
            payload=ApplicationProfileUpsertRequest(
                autosubmit_enabled=False,
                current_company="ManualCorp",
                target_work_country="Canada",
            ),
        )

        store.upsert_resume(
            user_id=user_id,
            payload=ResumeUpsertRequest(
                filename="resume.txt",
                resume_text=(
                    "Senior Staff Software Engineer at ResumeCorp\n"
                    "Location: Austin, Texas, United States\n"
                    "Built cloud automation systems."
                ),
            ),
        )
        profile = store.get_application_profile(user_id)
        assert profile is not None
        # Manual fields are never overwritten.
        assert profile.current_company == "ManualCorp"
        assert profile.target_work_country == "Canada"
        # Empty fields are populated from resume extraction.
        assert profile.most_recent_company == "ResumeCorp"
        assert profile.current_title == "Senior Staff Software Engineer"
        assert profile.target_work_city == "Austin"
        assert profile.target_work_state == "Texas"
