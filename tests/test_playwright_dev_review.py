from __future__ import annotations

import sys
import types
from typing import Any

import cloud_automation.services as cloud_services
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

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        del wait_until
        self.url = url

    def screenshot(self, path: str, full_page: bool = True) -> None:
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


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def new_page(self) -> _FakePage:
        return self._page

    def close(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def new_context(self) -> _FakeContext:
        return _FakeContext(self._page)

    def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.launch_kwargs: dict[str, Any] = {}

    def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launch_kwargs = kwargs
        return _FakeBrowser(self._page)


class _FakeSyncPlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self._chromium = chromium

    def __enter__(self) -> Any:
        return types.SimpleNamespace(chromium=self._chromium)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
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
        "playwright.sync_api",
        types.SimpleNamespace(sync_playwright=lambda: _FakeSyncPlaywright(fake_chromium)),
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

    def _fill_application_form(*, page: Any, request: ApplyRunRequest) -> None:
        del page, request
        filled["called"] = True

    def _manual_submit(*, page: Any, attempt: ApplyAttemptRecord) -> ApplyAttemptRecord:
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
        "_standard_terminal_attempt",
        lambda _attempt: (_ for _ in ()).throw(AssertionError("Standard terminal branch should not run")),
    )

    result = executor.complete_attempt(attempt=_build_attempt(), request=_build_request())
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

    def _fill_text_field(_page: Any, *, selectors: list[str], value: Any) -> bool:
        del selectors
        text_values.append(str(value))
        return True

    def _fill_boolean_field(_page: Any, *, selectors: list[str], value: bool) -> bool:
        del selectors
        boolean_values.append(value)
        return True

    monkeypatch.setattr(executor, "_fill_text_field", _fill_text_field)
    monkeypatch.setattr(executor, "_fill_boolean_field", _fill_boolean_field)

    executor._fill_application_form(page=object(), request=_build_request())

    assert "Jane Doe" in text_values
    assert "jane@example.com" in text_values
    assert "5551234567" in text_values
    assert "https://linkedin.com/in/janedoe" in text_values
    assert any("I am Jane Doe" in value for value in text_values)
    assert False in boolean_values
    assert True in boolean_values


def test_manual_submit_detection_marks_submitted_from_confirmation_url(monkeypatch) -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
        submit_timeout_seconds=5,
        poll_interval_ms=50,
    )
    page = _FakePage(url="https://jobs.example.com/thank-you")
    monkeypatch.setattr(executor, "_has_confirmation_text", lambda _page: False)

    result = executor._await_manual_submit(page=page, attempt=_build_attempt())

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
    monkeypatch.setattr(executor, "_has_confirmation_text", lambda _page: False)

    ticks = {"now": 0.0}

    def _fake_monotonic() -> float:
        ticks["now"] += 0.35
        return ticks["now"]

    monkeypatch.setattr(cloud_services.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(cloud_services.time, "sleep", lambda _seconds: None)

    result = executor._await_manual_submit(page=page, attempt=_build_attempt())

    assert result.status == ApplyAttemptStatus.blocked
    assert result.failure_code == FailureCode.manual_review_timeout
    assert "Manual submit not detected" in (result.failure_reason or "")
