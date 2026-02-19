from __future__ import annotations

import asyncio
import base64
import sys
import types
from typing import Any

import cloud_automation.services as cloud_services
import cloud_automation.services_legacy as cloud_services_legacy
from common.time import utc_now
from cloud_automation.models import (
    ApplyAttemptRecord,
    ApplyAttemptStatus,
    ApplyJob,
    ApplyRunRequest,
    FailureCode,
)
from cloud_automation.services import FormAnswerSynthesizer, PlaywrightApplyExecutor


class _FakePage:
    def __init__(self, *, url: str) -> None:
        self.url = url
        self.listeners: dict[str, Any] = {}

    def set_default_navigation_timeout(self, _value: int) -> None:
        return None

    def set_default_timeout(self, _value: int) -> None:
        return None

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        del wait_until
        self.url = url

    async def screenshot(self, path: str, full_page: bool = True) -> None:
        del path, full_page
        return None

    def on(self, event_name: str, handler: Any) -> None:
        self.listeners[event_name] = handler

    def remove_listener(self, event_name: str, handler: Any) -> None:
        current = self.listeners.get(event_name)
        if current is handler:
            self.listeners.pop(event_name, None)

    def click(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Submit click should never be invoked in dev review mode")


class _FakeFileInput:
    def __init__(self) -> None:
        self.uploaded_path: str | None = None

    async def is_visible(self, timeout: int = 200) -> bool:
        del timeout
        return True

    async def set_input_files(self, path: str, timeout: int = 0) -> None:
        del timeout
        self.uploaded_path = path


class _FakeLocator:
    def __init__(self, candidates: list[Any]) -> None:
        self._candidates = candidates

    async def count(self) -> int:
        return len(self._candidates)

    def nth(self, index: int) -> Any:
        return self._candidates[index]


class _FakeUploadPage:
    def __init__(self, file_input: _FakeFileInput) -> None:
        self._file_input = file_input

    def locator(self, selector: str) -> _FakeLocator:
        if selector == "input[type='file']#resume":
            return _FakeLocator([self._file_input])
        return _FakeLocator([])


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page

    async def close(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_context(self) -> _FakeContext:
        return _FakeContext(self._page)

    async def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.launch_kwargs: dict[str, Any] = {}

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launch_kwargs = kwargs
        return _FakeBrowser(self._page)


class _FakeAsyncPlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self._chromium = chromium

    async def __aenter__(self) -> Any:
        return types.SimpleNamespace(chromium=self._chromium)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        del exc_type, exc, tb
        return False


def _build_request() -> ApplyRunRequest:
    return ApplyRunRequest(
        user_ref="user-1",
        jobs=[
            ApplyJob(
                external_job_id="job-1",
                title="Backend Engineer",
                company="Acme",
                apply_url="https://jobs.example.com/apply/backend",
            )
        ],
        profile_payload={
            "full_name": "Jane Doe",
            "email": "jane@example.com",
            "application_profile": {
                "phone": "5551234567",
                "city": "Austin",
                "state": "TX",
                "country": "USA",
                "linkedin_url": "https://linkedin.com/in/janedoe",
                "github_url": "https://github.com/janedoe",
                "portfolio_url": "https://janedoe.dev",
                "work_authorization": "Authorized to work in the United States",
                "requires_sponsorship": False,
                "willing_to_relocate": True,
                "years_experience": 6,
                "custom_answers": [],
                "sensitive": {
                    "gender": "decline_to_answer",
                    "race_ethnicity": "decline_to_answer",
                    "veteran_status": "decline_to_answer",
                    "disability_status": "decline_to_answer",
                },
            },
            "preferences": {"interests": ["backend", "python"]},
            "resume_text": "Backend engineer focused on Python and distributed systems.",
        },
        daily_cap=25,
    )


def _build_attempt() -> ApplyAttemptRecord:
    return ApplyAttemptRecord(
        attempt_id="attempt-1",
        external_job_id="job-1",
        job_url="https://jobs.example.com/apply/backend",
        status=ApplyAttemptStatus.filling,
    )


def test_dev_review_mode_uses_manual_submit_branch_without_submit_click(monkeypatch) -> None:
    fake_page = _FakePage(url="https://jobs.example.com/apply/backend")
    fake_chromium = _FakeChromium(fake_page)
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: _FakeAsyncPlaywright(fake_chromium)),
    )

    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
        submit_timeout_seconds=30,
        poll_interval_ms=50,
        slow_mo_ms=0,
    )
    filled = {"called": False}
    reviewed = {"called": False}

    async def _fill_application_form(*, page: Any, request: ApplyRunRequest) -> None:
        del page, request
        filled["called"] = True

    async def _manual_submit(*, page: Any, attempt: ApplyAttemptRecord) -> ApplyAttemptRecord:
        del page
        reviewed["called"] = True
        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.submitted,
                "submitted_at": utc_now(),
                "failure_code": None,
                "failure_reason": None,
            }
        )

    monkeypatch.setattr(executor, "_fill_application_form", _fill_application_form)
    monkeypatch.setattr(executor, "_await_manual_submit", _manual_submit)
    monkeypatch.setattr(
        executor,
        "_submit_and_confirm",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Submit-and-confirm branch should not run")),
    )

    result = asyncio.run(executor.complete_attempt(attempt=_build_attempt(), request=_build_request()))
    assert result.status == ApplyAttemptStatus.submitted
    assert filled["called"] is True
    assert reviewed["called"] is True
    assert fake_chromium.launch_kwargs["headless"] is False


def test_fill_application_form_uses_synthesized_profile_values(monkeypatch) -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    text_values: list[str] = []
    boolean_values: list[bool] = []

    async def _fill_text_field(_page: Any, *, selectors: list[str], value: Any) -> bool:
        del selectors
        text_values.append(str(value))
        return True

    async def _fill_boolean_field(_page: Any, *, selectors: list[str], value: bool) -> bool:
        del selectors
        boolean_values.append(value)
        return True

    monkeypatch.setattr(executor, "_fill_text_field", _fill_text_field)
    monkeypatch.setattr(executor, "_fill_boolean_field", _fill_boolean_field)

    asyncio.run(executor._fill_application_form(page=object(), request=_build_request()))

    assert "Jane Doe" in text_values
    assert "jane@example.com" in text_values
    assert "5551234567" in text_values
    assert "https://linkedin.com/in/janedoe" in text_values
    assert not any("I am Jane Doe" in value for value in text_values)
    assert False in boolean_values
    assert True in boolean_values


def test_non_dev_mode_uses_submit_and_confirm_branch(monkeypatch) -> None:
    fake_page = _FakePage(url="https://jobs.example.com/apply/backend")
    fake_chromium = _FakeChromium(fake_page)
    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        types.SimpleNamespace(async_playwright=lambda: _FakeAsyncPlaywright(fake_chromium)),
    )

    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=False,
        submit_timeout_seconds=30,
        poll_interval_ms=50,
        slow_mo_ms=0,
    )
    submitted = {"called": False}

    async def _fill_application_form(*, page: Any, request: ApplyRunRequest) -> None:
        del page, request
        return None

    async def _submit_and_confirm(*, page: Any, attempt: ApplyAttemptRecord) -> ApplyAttemptRecord:
        del page
        submitted["called"] = True
        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.submitted,
                "submitted_at": utc_now(),
                "failure_code": None,
                "failure_reason": None,
            }
        )

    monkeypatch.setattr(executor, "_fill_application_form", _fill_application_form)
    monkeypatch.setattr(executor, "_submit_and_confirm", _submit_and_confirm)
    monkeypatch.setattr(
        executor,
        "_await_manual_submit",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Manual review branch should not run")),
    )

    result = asyncio.run(executor.complete_attempt(attempt=_build_attempt(), request=_build_request()))
    assert result.status == ApplyAttemptStatus.submitted
    assert submitted["called"] is True


def test_non_dev_submit_blocks_when_required_fields_unresolved(monkeypatch) -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=False,
        submit_timeout_seconds=5,
        poll_interval_ms=50,
    )
    page = _FakePage(url="https://jobs.example.com/apply/backend")

    async def _unresolved(_page: Any) -> list[str]:
        return ["Email", "Privacy Notice"]

    monkeypatch.setattr(executor, "_audit_required_fields", _unresolved)

    result = asyncio.run(executor._submit_and_confirm(page=page, attempt=_build_attempt()))
    assert result.status == ApplyAttemptStatus.blocked
    assert result.failure_code == FailureCode.form_validation_failed
    assert "Email" in (result.failure_reason or "")


def test_resume_upload_sets_input_files_when_resume_file_present() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    file_input = _FakeFileInput()
    page = _FakeUploadPage(file_input=file_input)
    resume_bytes = b"resume content"
    request = _build_request().model_copy(
        update={
            "profile_payload": {
                **_build_request().profile_payload,
                "resume_file": {
                    "filename": "resume.txt",
                    "content_base64": base64.b64encode(resume_bytes).decode("ascii"),
                    "size_bytes": len(resume_bytes),
                },
            }
        }
    )

    uploaded = asyncio.run(executor._upload_resume_file(page=page, request=request))

    assert uploaded is True
    assert file_input.uploaded_path is not None


def test_manual_submit_detection_marks_submitted_from_confirmation_url(monkeypatch) -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
        submit_timeout_seconds=5,
        poll_interval_ms=50,
    )
    page = _FakePage(url="https://jobs.example.com/thank-you")
    async def _no_confirmation(_page: Any) -> bool:
        return False

    monkeypatch.setattr(executor, "_has_confirmation_text", _no_confirmation)

    result = asyncio.run(executor._await_manual_submit(page=page, attempt=_build_attempt()))

    assert result.status == ApplyAttemptStatus.submitted
    assert result.submitted_at is not None
    assert result.failure_code is None


def test_manual_submit_detection_timeout_marks_blocked(monkeypatch) -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
        submit_timeout_seconds=1,
        poll_interval_ms=50,
    )
    page = _FakePage(url="https://jobs.example.com/apply/backend")
    async def _no_confirmation(_page: Any) -> bool:
        return False

    monkeypatch.setattr(executor, "_has_confirmation_text", _no_confirmation)

    ticks = {"now": 0.0}

    def _fake_monotonic() -> float:
        ticks["now"] += 0.35
        return ticks["now"]

    monkeypatch.setattr(cloud_services.time, "monotonic", _fake_monotonic)
    async def _fake_async_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(cloud_services_legacy.asyncio, "sleep", _fake_async_sleep)

    result = asyncio.run(executor._await_manual_submit(page=page, attempt=_build_attempt()))

    assert result.status == ApplyAttemptStatus.blocked
    assert result.failure_code == FailureCode.manual_review_timeout
    assert "Manual submit not detected" in (result.failure_reason or "")
