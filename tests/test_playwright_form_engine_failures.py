from __future__ import annotations

import asyncio
from typing import Any

from cloud_automation.models import ApplyJob, ApplyRunRequest
from cloud_automation.services import FormAnswerSynthesizer, PlaywrightApplyExecutor


def _build_request() -> ApplyRunRequest:
    return ApplyRunRequest(
        user_ref="user-1",
        jobs=[
            ApplyJob(
                external_job_id="job-1",
                title="Engineer",
                company="Acme",
                apply_url="https://jobs.example.com/apply",
            )
        ],
        profile_payload={
            "full_name": "Riza Ingalls",
            "email": "riza@example.com",
            "resume_text": "Senior Engineer at ResumeCorp",
            "application_profile": {
                "phone": "5551234567",
                "city": "Austin",
                "state": "Texas",
                "country": "United States",
                "work_authorization": "Authorized to work in the United States",
                "requires_sponsorship": False,
                "willing_to_relocate": True,
                "custom_answers": [],
                "sensitive": {
                    "gender": "decline_to_answer",
                    "race_ethnicity": "decline_to_answer",
                    "veteran_status": "decline_to_answer",
                    "disability_status": "decline_to_answer",
                },
            },
            "preferences": {"interests": ["automation"]},
        },
        daily_cap=25,
    )


class _FakeComboboxCandidate:
    def __init__(self) -> None:
        self.value = ""

    async def click(self, timeout: int = 0) -> None:
        del timeout
        return None

    async def fill(self, value: str, timeout: int = 0) -> None:
        del timeout
        self.value = value

    async def type(self, value: str, delay: int = 0) -> None:
        del delay
        self.value = value

    async def input_value(self) -> str:
        return self.value

    async def evaluate(self, expression: str, *_args: Any) -> Any:
        if "return {" in expression and "options" in expression:
            return {"options": [], "selector": "", "reason": "no_options_found"}
        if "return false;" in expression and "target" in expression:
            return False
        if "el.value" in expression or "el.textContent" in expression:
            return self.value
        return None


class _NoOptionPage:
    async def evaluate(self, expression: str) -> Any:
        if "role='option'" in expression:
            return []
        return None

    def get_by_role(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Option lookup should not run without discovered options")


def test_resolve_dynamic_answer_combobox_returns_candidate_without_options() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=False,
    )
    request = _build_request()
    values = executor._build_fill_values(request)

    answer = executor._resolve_dynamic_answer(
        request=request,
        values=values,
        label="Are you currently authorized to work in the U.S.?",
        name="question_work_authorization",
        field_id="question_100",
        input_type="text",
        tag_name="input",
        role="combobox",
        helper_text="",
        options=[],
    )

    assert answer == "Authorized to work in the United States"


def test_resolve_dynamic_answer_native_select_remains_strict_without_options() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=False,
    )
    request = _build_request()
    values = executor._build_fill_values(request)

    answer = executor._resolve_dynamic_answer(
        request=request,
        values=values,
        label="Are you currently authorized to work in the U.S.?",
        name="question_work_authorization",
        field_id="question_100",
        input_type="",
        tag_name="select",
        role="",
        helper_text="",
        options=[],
    )

    assert answer is None


def test_combobox_without_options_is_not_committed_and_reports_category() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=False,
    )
    candidate = _FakeComboboxCandidate()
    page = _NoOptionPage()

    success = asyncio.run(
        executor._fill_combobox(
            page=page,
            candidate=candidate,
            value="United States",
            field_label="Are you located in Colorado, the UK, Switzerland, or EEA?",
            field_name="question_location_region",
            field_id="question_200",
        )
    )

    assert success is False
    assert candidate.value == ""
    assert executor._last_combobox_failure_category == "combobox_options_not_discovered"
    assert isinstance(executor._last_combobox_debug, dict)
    assert executor._last_combobox_debug.get("typed_probe_cleared") is True


def test_unresolved_summary_includes_ghost_required_ignored_bucket() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=False,
    )
    executor._required_audit_stats = {"ghost_required_ignored": 2}

    summary = executor._build_unresolved_summary([])
    assert summary["ghost_required_ignored"] == 2
