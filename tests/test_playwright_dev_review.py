from __future__ import annotations

import asyncio
import base64
import sys
import types
from typing import Any

import cloud_automation.services.playwright as playwright_services
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
    def __init__(
        self,
        *,
        field_id: str = "resume",
        field_name: str = "resume",
        aria_label: str = "resume",
        label: str = "resume",
        helper: str = "upload your resume",
    ) -> None:
        self.uploaded_path: str | None = None
        self.uploaded_payload: dict[str, Any] | None = None
        self.field_id = field_id
        self.field_name = field_name
        self.aria_label = aria_label
        self.label = label
        self.helper = helper

    async def is_visible(self, timeout: int = 200) -> bool:
        del timeout
        return True

    async def set_input_files(self, path: Any, timeout: int = 0) -> None:
        del timeout
        if isinstance(path, dict):
            self.uploaded_payload = path
            self.uploaded_path = None
        else:
            self.uploaded_path = str(path)
            self.uploaded_payload = None

    async def evaluate(self, expression: str) -> Any:
        has_upload = bool(self.uploaded_path) or bool(self.uploaded_payload)
        uploaded_name = ""
        if self.uploaded_payload:
            uploaded_name = str(self.uploaded_payload.get("name", ""))
        elif self.uploaded_path:
            uploaded_name = str(self.uploaded_path).split("/")[-1]
        if "first_name" in expression and "count" in expression:
            return {"count": 1 if has_upload else 0, "first_name": uploaded_name}
        if "files && el.files.length" in expression:
            return 1 if has_upload else 0
        return {
            "id": self.field_id,
            "name": self.field_name,
            "aria": self.aria_label,
            "accept": ".pdf,.doc,.docx",
            "label": self.label,
            "helper": self.helper,
            "visible": True,
            "required": True,
        }


class _FakeLocator:
    def __init__(self, candidates: list[Any]) -> None:
        self._candidates = candidates

    async def count(self) -> int:
        return len(self._candidates)

    def nth(self, index: int) -> Any:
        return self._candidates[index]


class _FakeUploadPage:
    def __init__(
        self,
        file_input: _FakeFileInput,
        *,
        include_cover_letter: bool = False,
    ) -> None:
        self._resume_input = file_input
        self._cover_input = (
            _FakeFileInput(
                field_id="cover_letter",
                field_name="cover_letter",
                aria_label="cover letter",
                label="cover letter",
                helper="upload cover letter",
            )
            if include_cover_letter
            else None
        )

    def locator(self, selector: str) -> _FakeLocator:
        if selector == "input[type='file']":
            items = [self._resume_input]
            if self._cover_input is not None:
                items.append(self._cover_input)
            return _FakeLocator(items)
        if selector == "input[type='file']#resume":
            return _FakeLocator([self._resume_input])
        if selector in {
            "input[type='file'][name*='resume']",
            "input[type='file'][id*='resume']",
        }:
            return _FakeLocator([self._resume_input])
        if self._cover_input is not None and selector in {
            "input[type='file'][name*='cover']",
            "input[type='file'][id*='cover']",
            "input[type='file'][name*='letter']",
            "input[type='file'][id*='letter']",
        }:
            return _FakeLocator([self._cover_input])
        return _FakeLocator([])


class _FakeVisibleElement:
    async def is_visible(self, timeout: int = 200) -> bool:
        del timeout
        return True

    async def is_disabled(self) -> bool:
        return False

    async def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        del timeout
        return None


class _FakeBootstrapButton(_FakeVisibleElement):
    def __init__(self, page: "_FakeBootstrapPage") -> None:
        self._page = page
        self.click_count = 0

    async def click(self, timeout: int = 0) -> None:
        del timeout
        self.click_count += 1
        self._page.form_visible = True
        self._page.url = self._page.url.rstrip("/") + "/apply"


class _FakeBootstrapPage:
    def __init__(self, *, url: str, has_cta: bool) -> None:
        self.url = url
        self.has_cta = has_cta
        self.form_visible = False
        self._apply_button = _FakeBootstrapButton(self)
        self._visible_field = _FakeVisibleElement()

    def locator(self, selector: str) -> _FakeLocator:
        if selector in {
            "button:has-text('Apply for this job')",
            "a:has-text('Apply for this job')",
            "button:has-text('Apply')",
            "a[href*='/apply']",
        }:
            if self.has_cta and not self.form_visible:
                return _FakeLocator([self._apply_button])
            return _FakeLocator([])
        if selector in {
            "input[type='file']",
            "input[type='email']",
            "input[name*='first'][name*='name']",
            "input[name*='full'][name*='name']",
            "textarea",
            "select",
        }:
            if self.form_visible:
                return _FakeLocator([self._visible_field])
            return _FakeLocator([])
        return _FakeLocator([])

    async def evaluate(self, expression: str) -> Any:
        if "visibleCount" in expression and "requiredVisibleCount" in expression:
            if self.form_visible:
                return {"visibleCount": 6, "requiredVisibleCount": 3, "visibleFileInputs": 1}
            return {"visibleCount": 0, "requiredVisibleCount": 0, "visibleFileInputs": 0}
        return None


class _FakeKeyboard:
    def __init__(self) -> None:
        self.press_count = 0

    async def press(self, _key: str) -> None:
        self.press_count += 1


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

    async def evaluate(self, expression: str) -> Any:
        if "el.value" in expression or "el.textContent" in expression:
            return self.value
        return None


class _FakeRoleOption:
    async def click(self, timeout: int = 0) -> None:
        del timeout
        return None


class _FakeRoleOptionLocator:
    @property
    def first(self) -> _FakeRoleOption:
        return _FakeRoleOption()


class _FakeComboboxPage:
    def __init__(self, options: list[str]) -> None:
        self.options = options
        self.keyboard = _FakeKeyboard()

    async def evaluate(self, expression: str) -> Any:
        if "role='option'" in expression:
            return self.options
        return None

    def get_by_role(self, role: str, name: Any) -> _FakeRoleOptionLocator:
        del role, name
        return _FakeRoleOptionLocator()


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

    async def _form_visible(**_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(executor, "_fill_application_form", _fill_application_form)
    monkeypatch.setattr(executor, "_ensure_application_form_visible", _form_visible)
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

    async def _fill_text_field(
        _page: Any,
        *,
        selectors: list[str],
        value: Any,
        field_label: str | None = None,
    ) -> bool:
        del selectors
        del field_label
        text_values.append(str(value))
        return True

    async def _fill_boolean_field(
        _page: Any,
        *,
        selectors: list[str],
        value: bool,
        field_label: str | None = None,
    ) -> bool:
        del selectors
        del field_label
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

    async def _form_visible(**_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(executor, "_fill_application_form", _fill_application_form)
    monkeypatch.setattr(executor, "_ensure_application_form_visible", _form_visible)
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
    assert file_input.uploaded_payload is not None
    assert file_input.uploaded_payload.get("name") == "resume.txt"


def test_resolve_dynamic_answer_maps_authorized_select_to_yes() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    request = _build_request()
    values = executor._build_fill_values(request)
    executor._active_request = request
    executor._current_values = values

    resolved = executor._resolve_dynamic_answer(
        request=request,
        values=values,
        label="Are you authorized to work in the country for which you applied?",
        name="question_authorized",
        field_id="question_123",
        input_type="",
        tag_name="select",
        role="",
        helper_text="",
        options=["Select...", "Yes", "No"],
    )

    assert resolved == "Yes"


def test_resolve_dynamic_answer_uses_textarea_tag_for_open_text() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    request = _build_request()
    values = executor._build_fill_values(request)

    resolved = executor._resolve_dynamic_answer(
        request=request,
        values=values,
        label="Why do you want to join Figma?",
        name="question_why_join",
        field_id="question_999",
        input_type="",
        tag_name="textarea",
        role="",
        helper_text="Please share 3-4 sentences.",
        options=[],
    )

    assert isinstance(resolved, str)
    assert resolved.strip()


def test_build_artifacts_includes_field_trace_when_present() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    executor._latest_trace_file_path = "/tmp/attempt-1-playwright-field-trace.json"

    artifacts = executor._build_artifacts("attempt-1")
    kinds = {artifact.kind for artifact in artifacts}

    assert "html" in kinds
    assert "field_trace" in kinds


def test_bootstrap_clicks_apply_gate_for_lever() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    page = _FakeBootstrapPage(url="https://jobs.lever.co/findem/job-1", has_cta=True)

    ready = asyncio.run(executor._ensure_application_form_visible(page=page))

    assert ready is True
    assert executor._bootstrap_outcome in {"visible_after_click", "visible_after_poll"}
    assert any(action.get("step") == "bootstrap_click" and action.get("clicked") for action in executor._bootstrap_actions)


def test_bootstrap_returns_false_when_form_never_visible() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    page = _FakeBootstrapPage(url="https://jobs.lever.co/findem/job-1", has_cta=False)

    ready = asyncio.run(executor._ensure_application_form_visible(page=page))

    assert ready is False
    assert executor._bootstrap_outcome == "form_not_visible_pre_fill"


def test_resolve_dynamic_answer_short_fact_current_company_returns_none_without_data() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    request = _build_request()
    values = executor._build_fill_values(request)

    resolved = executor._resolve_dynamic_answer(
        request=request,
        values=values,
        label="Please provide the name of your current (or most recent) company",
        name="question_current_company",
        field_id="question_200",
        input_type="text",
        tag_name="input",
        role="",
        helper_text="",
        options=[],
    )

    assert resolved is None


def test_resolve_dynamic_answer_short_fact_current_company_uses_custom_answer() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    request = _build_request().model_copy(
        update={
            "profile_payload": {
                **_build_request().profile_payload,
                "application_profile": {
                    **_build_request().profile_payload["application_profile"],
                    "custom_answers": [
                        {
                            "question_key": "current_company",
                            "answer": "ExampleCorp",
                        }
                    ],
                },
            }
        }
    )
    values = executor._build_fill_values(request)

    resolved = executor._resolve_dynamic_answer(
        request=request,
        values=values,
        label="Please provide the name of your current (or most recent) company",
        name="question_current_company",
        field_id="question_200",
        input_type="text",
        tag_name="input",
        role="",
        helper_text="",
        options=[],
    )

    assert resolved == "ExampleCorp"


def test_resume_upload_does_not_reuse_resume_for_cover_letter_slot() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    resume_input = _FakeFileInput(
        field_id="resume",
        field_name="resume",
        aria_label="resume",
        label="resume",
        helper="upload your resume",
    )
    page = _FakeUploadPage(file_input=resume_input, include_cover_letter=True)
    resume_bytes = b"resume content"
    request = _build_request().model_copy(
        update={
            "profile_payload": {
                **_build_request().profile_payload,
                "resume_file": {
                    "filename": "Riza-Ingalls-Resume.pdf",
                    "content_base64": base64.b64encode(resume_bytes).decode("ascii"),
                    "size_bytes": len(resume_bytes),
                    "mime_type": "application/pdf",
                },
            }
        }
    )

    uploaded = asyncio.run(executor._upload_resume_file(page=page, request=request))

    assert uploaded is True
    assert resume_input.uploaded_payload is not None
    assert resume_input.uploaded_payload.get("name") == "Riza-Ingalls-Resume.pdf"
    assert page._cover_input is not None
    assert page._cover_input.uploaded_payload is None


def test_combobox_does_not_commit_arbitrary_text_without_option_match() -> None:
    executor = PlaywrightApplyExecutor(
        synthesizer=FormAnswerSynthesizer(),
        dev_review_mode=True,
    )
    page = _FakeComboboxPage(options=[])
    candidate = _FakeComboboxCandidate()

    success = asyncio.run(
        executor._fill_combobox(
            page=page,
            candidate=candidate,
            value="United States",
            field_label="Are you located in Colorado (US) ...?",
            field_name="question_region",
            field_id="question_300",
        )
    )

    assert success is False
    assert page.keyboard.press_count == 0


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

    monkeypatch.setattr(playwright_services.time, "monotonic", _fake_monotonic)
    async def _fake_async_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(playwright_services.asyncio, "sleep", _fake_async_sleep)

    result = asyncio.run(executor._await_manual_submit(page=page, attempt=_build_attempt()))

    assert result.status == ApplyAttemptStatus.blocked
    assert result.failure_code == FailureCode.manual_review_timeout
    assert "Manual submit not detected" in (result.failure_reason or "")
