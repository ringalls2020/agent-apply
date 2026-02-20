from __future__ import annotations

import asyncio
import base64
import binascii
from collections import defaultdict
import json
import logging
import os
import re
import time
from contextlib import suppress
from datetime import timedelta
from typing import Any

from common.time import utc_now

from ..models import (
    ApplyAttemptRecord,
    ApplyAttemptStatus,
    ApplyRunRequest,
    ArtifactRef,
    FailureCode,
)
from .answers import FormAnswerSynthesizer
from .form_engine import (
    FieldFillAttempt,
    FormFillDiagnostics,
    best_option_match,
    capture_form_snapshot,
    classify_field_intent,
    classify_file_slot,
    classify_widget_type,
    diagnostics_to_dict,
    group_fields_by_semantic_key,
    normalize_key,
    is_option_constrained,
    is_placeholder_value,
    normalize_text,
    should_ignore_internal_field_candidate,
)

logger = logging.getLogger(__name__)

class PlaywrightApplyExecutor:
    _SUBMIT_URL_TOKENS = (
        "submitted",
        "thank-you",
        "thank_you",
        "confirmation",
        "success",
        "complete",
        "receipt",
    )
    _SUBMIT_TEXT_RE = re.compile(
        r"(application\s+(has\s+been\s+)?submitted|thank\s+you\s+for\s+applying|your\s+application\s+(has\s+been|was)\s+received|application\s+received)",
        re.IGNORECASE,
    )
    _LEVER_APPLY_SELECTORS = (
        "button:has-text('Apply for this job')",
        "a:has-text('Apply for this job')",
        "button:has-text('Apply')",
        "a[href*='/apply']",
    )
    _FORM_VISIBILITY_SELECTORS = (
        "input[type='file']",
        "input[type='email']",
        "input[name*='first'][name*='name']",
        "input[name*='full'][name*='name']",
        "textarea",
        "select",
    )

    def __init__(
        self,
        *,
        synthesizer: FormAnswerSynthesizer,
        dev_review_mode: bool = False,
        submit_timeout_seconds: int = 300,
        poll_interval_ms: int = 500,
        slow_mo_ms: int = 120,
    ) -> None:
        self.synthesizer = synthesizer
        self.dev_review_mode = dev_review_mode
        self.headless = (
            os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if self.dev_review_mode and self.headless:
            logger.info("playwright_headless_overridden_for_dev_review_mode")
            self.headless = False
        self.nav_timeout_ms = int(float(os.getenv("PLAYWRIGHT_NAV_TIMEOUT_SECONDS", "20")) * 1000)
        self.action_timeout_ms = int(float(os.getenv("PLAYWRIGHT_ACTION_TIMEOUT_SECONDS", "5")) * 1000)
        self.capture_screenshots = (
            os.getenv("PLAYWRIGHT_CAPTURE_SCREENSHOTS", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.submit_timeout_seconds = max(int(submit_timeout_seconds), 1)
        self.poll_interval_seconds = max(int(poll_interval_ms), 50) / 1000.0
        self.slow_mo_ms = max(int(slow_mo_ms), 0)
        self.form_engine_v2_enabled = (
            os.getenv("PLAYWRIGHT_FORM_ENGINE_V2_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.form_max_field_retries = max(
            int(os.getenv("PLAYWRIGHT_FORM_MAX_FIELD_RETRIES", "3")),
            1,
        )
        self.form_llm_fallback_enabled = (
            os.getenv("PLAYWRIGHT_FORM_LLM_FALLBACK_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        try:
            self.form_llm_min_confidence = float(
                os.getenv("PLAYWRIGHT_FORM_LLM_MIN_CONFIDENCE", "0.65")
            )
        except ValueError:
            self.form_llm_min_confidence = 0.65
        self.form_llm_min_confidence = max(0.0, min(self.form_llm_min_confidence, 1.0))
        self.form_llm_max_calls = max(
            int(os.getenv("PLAYWRIGHT_FORM_LLM_MAX_CALLS", "12")),
            1,
        )
        self.short_fact_llm_fallback_enabled = (
            os.getenv("PLAYWRIGHT_SHORT_FACT_LLM_FALLBACK_ENABLED", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._field_attempt_traces: list[FieldFillAttempt] = []
        self._latest_form_diagnostics: dict[str, Any] | None = None
        self._latest_trace_file_path: str | None = None
        self._active_request: ApplyRunRequest | None = None
        self._current_values: dict[str, str | bool] = {}
        self._llm_option_cache: dict[str, str | None] = {}
        self._llm_calls_used: int = 0
        self._bootstrap_actions: list[dict[str, Any]] = []
        self._bootstrap_outcome: str = "not_started"
        self._last_combobox_failure_category: str | None = None
        self._last_combobox_debug: dict[str, Any] | None = None
        self._combobox_trace: list[dict[str, Any]] = []
        self._required_audit_stats: dict[str, int] = {}
        self._last_required_unresolved: list[str] = []
        self._field_failure_repeats: dict[tuple[str, str], int] = {}
        self._runtime_short_fact_cache: dict[str, str | None] = {}

    async def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
        self._field_attempt_traces = []
        self._latest_form_diagnostics = None
        self._latest_trace_file_path = None
        self._bootstrap_actions = []
        self._bootstrap_outcome = "not_started"
        self._llm_option_cache = {}
        self._llm_calls_used = 0
        self._last_combobox_failure_category = None
        self._last_combobox_debug = None
        self._combobox_trace = []
        self._required_audit_stats = {}
        self._last_required_unresolved = []
        self._field_failure_repeats = {}
        self._runtime_short_fact_cache = {}
        preflight_failure = self._preflight_failure(attempt=attempt, request=request)
        if preflight_failure is not None:
            return preflight_failure

        browser = None
        context = None
        try:
            from playwright.async_api import async_playwright

            launch_kwargs: dict[str, Any] = {"headless": self.headless}
            if self.dev_review_mode and self.slow_mo_ms > 0:
                launch_kwargs["slow_mo"] = self.slow_mo_ms

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**launch_kwargs)
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_navigation_timeout(self.nav_timeout_ms)
                page.set_default_timeout(self.action_timeout_ms)
                await page.goto(attempt.job_url, wait_until="domcontentloaded")
                form_ready = await self._ensure_application_form_visible(page=page)
                if not form_ready:
                    diagnostics = {
                        "engine_version": "v2" if self.form_engine_v2_enabled else "legacy",
                        "filled_fields": 0,
                        "unresolved_required": ["application_form_not_visible"],
                        "unresolved_summary": {"form_not_visible_pre_fill": 1},
                        "llm_calls_used": self._llm_calls_used,
                        "llm_call_budget": self.form_llm_max_calls,
                        "combobox_trace": [],
                        "required_audit_stats": self._required_audit_stats,
                        "bootstrap_actions": self._bootstrap_actions,
                        "bootstrap_outcome": self._bootstrap_outcome,
                        "stages": [],
                        "field_snapshots": [],
                    }
                    self._latest_form_diagnostics = diagnostics
                    self._latest_trace_file_path = self._persist_form_trace(
                        attempt_id=attempt.attempt_id,
                        diagnostics=diagnostics,
                    )
                    if self.capture_screenshots:
                        await page.screenshot(path=f"/tmp/{attempt.attempt_id}.png", full_page=True)
                    return attempt.model_copy(
                        update={
                            "status": ApplyAttemptStatus.blocked,
                            "failure_code": FailureCode.form_validation_failed,
                            "failure_reason": "form_not_visible_pre_fill",
                            "submitted_at": None,
                            "artifacts": self._build_artifacts(attempt.attempt_id),
                        }
                    )
                diagnostics = await self._fill_application_form(page=page, request=request)
                self._latest_form_diagnostics = diagnostics
                self._latest_trace_file_path = self._persist_form_trace(
                    attempt_id=attempt.attempt_id,
                    diagnostics=diagnostics,
                )

                if self.capture_screenshots:
                    await page.screenshot(path=f"/tmp/{attempt.attempt_id}.png", full_page=True)

                if self.dev_review_mode:
                    terminal_attempt = await self._await_manual_submit(
                        page=page,
                        attempt=attempt,
                    )
                else:
                    terminal_attempt = await self._submit_and_confirm(
                        page=page,
                        attempt=attempt,
                    )
                return terminal_attempt.model_copy(
                    update={"artifacts": self._build_artifacts(attempt.attempt_id)}
                )
        except Exception as exc:
            logger.exception(
                "playwright_apply_attempt_failed",
                extra={"attempt_id": attempt.attempt_id, "job_url": attempt.job_url},
            )
            error_text = str(exc).lower()
            failure_code = (
                FailureCode.timeout if "timeout" in error_text else FailureCode.site_blocked
            )
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": failure_code,
                    "failure_reason": str(exc),
                }
            )
        finally:
            if context is not None:
                with suppress(Exception):
                    await context.close()
            if browser is not None:
                with suppress(Exception):
                    await browser.close()

    @staticmethod
    def _application_profile(request: ApplyRunRequest) -> dict[str, Any]:
        profile_payload = request.profile_payload or {}
        profile = profile_payload.get("application_profile")
        return profile if isinstance(profile, dict) else {}

    def _preflight_failure(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord | None:
        lower_url = attempt.job_url.lower()
        if "captcha" in lower_url:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": FailureCode.captcha_failed,
                    "failure_reason": "CAPTCHA challenge detected",
                }
            )
        if "blocked" in lower_url:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": FailureCode.site_blocked,
                    "failure_reason": "Site automation protections blocked navigation",
                }
            )

        work_auth = self.synthesizer.resolve_typed_answer(
            request=request,
            question_key="work_authorization",
        )
        if not work_auth:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.failed,
                    "failure_code": FailureCode.form_validation_failed,
                    "failure_reason": "Missing work authorization answer in application profile",
                }
            )
        return None

    @staticmethod
    def _split_name(full_name: str) -> tuple[str, str]:
        tokens = [part for part in full_name.split() if part]
        if not tokens:
            return "", ""
        if len(tokens) == 1:
            return tokens[0], ""
        return tokens[0], " ".join(tokens[1:])

    def _build_fill_values(self, request: ApplyRunRequest) -> dict[str, str | bool]:
        profile_payload = request.profile_payload or {}
        profile = self._application_profile(request)
        full_name = str(profile_payload.get("full_name", "")).strip()
        first_name, last_name = self._split_name(full_name)
        email = str(profile_payload.get("email", "")).strip()
        work_auth = self.synthesizer.resolve_typed_answer(
            request=request,
            question_key="work_authorization",
        ) or ""
        cover_letter = self.synthesizer.answer_question(
            request=request,
            label="Please provide a short, role-specific cover letter",
            name="cover_letter",
            options=None,
        )

        values: dict[str, str | bool] = {
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": str(profile.get("phone", "")).strip(),
            "city": str(profile.get("city", "")).strip(),
            "state": str(profile.get("state", "")).strip(),
            "country": str(profile.get("country", "")).strip(),
            "linkedin": str(profile.get("linkedin_url", "")).strip(),
            "github": str(profile.get("github_url", "")).strip(),
            "portfolio": str(profile.get("portfolio_url", "")).strip(),
            "work_authorization": work_auth,
            "requires_sponsorship": bool(profile.get("requires_sponsorship")),
            "willing_to_relocate": bool(profile.get("willing_to_relocate")),
            "years_experience": str(profile.get("years_experience", "")).strip(),
            "cover_letter": cover_letter.strip(),
            "current_company": str(
                self.synthesizer.resolve_typed_answer(
                    request=request,
                    question_key="current_company",
                )
                or ""
            ).strip(),
            "most_recent_company": str(
                self.synthesizer.resolve_typed_answer(
                    request=request,
                    question_key="most_recent_company",
                )
                or ""
            ).strip(),
            "current_title": str(
                self.synthesizer.resolve_typed_answer(
                    request=request,
                    question_key="current_title",
                )
                or ""
            ).strip(),
            "target_work_city": str(
                self.synthesizer.resolve_typed_answer(
                    request=request,
                    question_key="target_work_city",
                )
                or ""
            ).strip(),
            "target_work_state": str(
                self.synthesizer.resolve_typed_answer(
                    request=request,
                    question_key="target_work_state",
                )
                or ""
            ).strip(),
            "target_work_country": str(
                self.synthesizer.resolve_typed_answer(
                    request=request,
                    question_key="target_work_country",
                )
                or ""
            ).strip(),
        }
        return values

    @staticmethod
    def _option_choice_cache_key(
        *,
        label: str | None,
        name: str | None,
        field_id: str | None,
        options: list[str],
        preferred_value: str | None,
        allow_llm: bool,
    ) -> str:
        normalized_options = [
            normalize_key(str(option).strip()) for option in options if str(option).strip()
        ]
        normalized_options = [item for item in normalized_options if item]
        return "::".join(
            [
                normalize_key(label or ""),
                normalize_key(name or ""),
                normalize_key(field_id or ""),
                normalize_key(preferred_value or ""),
                "1" if allow_llm else "0",
                ",".join(normalized_options[:40]),
            ]
        )

    def _choose_option_value(
        self,
        *,
        label: str | None,
        name: str | None,
        field_id: str | None,
        options: list[str],
        preferred_value: str | None = None,
        allow_llm_fallback: bool | None = None,
    ) -> str | None:
        request = self._active_request
        if request is None:
            return best_option_match(preferred_value or "", options)

        allow_llm = (
            self.form_llm_fallback_enabled
            if allow_llm_fallback is None
            else bool(allow_llm_fallback)
        )
        if self._llm_calls_used >= self.form_llm_max_calls:
            allow_llm = False

        cache_key = self._option_choice_cache_key(
            label=label,
            name=name,
            field_id=field_id,
            options=options,
            preferred_value=preferred_value,
            allow_llm=allow_llm,
        )
        if cache_key in self._llm_option_cache:
            return self._llm_option_cache[cache_key]

        if allow_llm:
            self._llm_calls_used += 1

        selected = self.synthesizer.choose_option_value(
            request=request,
            label=label,
            name=name,
            field_id=field_id,
            options=options,
            preferred_value=preferred_value,
            allow_llm_fallback=allow_llm,
            min_confidence=self.form_llm_min_confidence,
        )
        self._llm_option_cache[cache_key] = selected
        return selected

    @staticmethod
    def _resume_file_payload(request: ApplyRunRequest) -> dict[str, Any] | None:
        profile_payload = request.profile_payload or {}
        resume_file = profile_payload.get("resume_file")
        return resume_file if isinstance(resume_file, dict) else None

    @staticmethod
    def _cover_letter_file_payload(request: ApplyRunRequest) -> dict[str, Any] | None:
        profile_payload = request.profile_payload or {}
        file_payload = profile_payload.get("cover_letter_file")
        return file_payload if isinstance(file_payload, dict) else None

    @staticmethod
    def _provider_from_url(url: str | None) -> str:
        normalized = normalize_text(url)
        if "jobs.lever.co" in normalized or "/lever/" in normalized:
            return "lever"
        if "boards.greenhouse.io" in normalized or "job-boards.greenhouse.io" in normalized:
            return "greenhouse"
        return "generic"

    async def _has_visible_form_indicator(self, *, page: Any) -> bool:
        if hasattr(page, "evaluate"):
            with suppress(Exception):
                metrics = await page.evaluate(
                    """() => {
                        const isVisible = (el) => {
                            const style = window.getComputedStyle(el);
                            if (style.visibility === "hidden" || style.display === "none") return false;
                            const rect = el.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        };
                        const fields = Array.from(document.querySelectorAll("input, textarea, select"));
                        const visible = fields.filter((el) => isVisible(el));
                        const requiredVisible = visible.filter((el) =>
                            Boolean(el.required) || String(el.getAttribute("aria-required") || "").toLowerCase() === "true"
                        );
                        const visibleFileInputs = visible.filter((el) =>
                            String(el.getAttribute("type") || "").toLowerCase() === "file"
                        );
                        return {
                            visibleCount: visible.length,
                            requiredVisibleCount: requiredVisible.length,
                            visibleFileInputs: visibleFileInputs.length,
                        };
                    }"""
                )
                if isinstance(metrics, dict):
                    visible_count = int(metrics.get("visibleCount", 0))
                    required_count = int(metrics.get("requiredVisibleCount", 0))
                    file_count = int(metrics.get("visibleFileInputs", 0))
                    if required_count > 0 and (visible_count >= 3 or file_count > 0):
                        return True
                    if visible_count >= 5:
                        return True

        if not hasattr(page, "locator"):
            return False
        for selector in self._FORM_VISIBILITY_SELECTORS:
            locator = page.locator(selector)
            count = 0
            with suppress(Exception):
                count = await locator.count()
            if count <= 0:
                continue
            for index in range(min(count, 3)):
                candidate = locator.nth(index)
                with suppress(Exception):
                    if await candidate.is_visible(timeout=200):
                        return True
        return False

    async def _wait_for_form_visible(
        self,
        *,
        page: Any,
        timeout_ms: int,
    ) -> bool:
        deadline = time.monotonic() + max(timeout_ms, 200) / 1000.0
        while time.monotonic() < deadline:
            if await self._has_visible_form_indicator(page=page):
                return True
            await asyncio.sleep(0.2)
        return False

    async def _click_visible_selector(self, *, page: Any, selector: str) -> bool:
        if not hasattr(page, "locator"):
            return False
        locator = page.locator(selector)
        count = 0
        with suppress(Exception):
            count = await locator.count()
        for index in range(min(count, 4)):
            candidate = locator.nth(index)
            with suppress(Exception):
                if not await candidate.is_visible(timeout=200):
                    continue
                with suppress(Exception):
                    await candidate.scroll_into_view_if_needed(timeout=min(self.action_timeout_ms, 1000))
                disabled = False
                with suppress(Exception):
                    disabled = bool(await candidate.is_disabled())
                if disabled:
                    continue
                await candidate.click(timeout=self.action_timeout_ms)
                return True
        return False

    async def _ensure_application_form_visible(self, *, page: Any) -> bool:
        self._bootstrap_actions = []
        provider = self._provider_from_url(getattr(page, "url", None))

        if await self._has_visible_form_indicator(page=page):
            self._bootstrap_outcome = "already_visible"
            self._bootstrap_actions.append(
                {"step": "initial_visibility_check", "provider": provider, "ready": True}
            )
            return True

        selectors: tuple[str, ...]
        if provider == "lever":
            selectors = self._LEVER_APPLY_SELECTORS
        else:
            selectors = (
                "button:has-text('Apply for this job')",
                "a:has-text('Apply for this job')",
                "button:has-text('Apply now')",
                "button:has-text('Apply')",
                "a[href*='apply']",
            )

        for attempt_index in range(1, 4):
            for selector in selectors:
                clicked = await self._click_visible_selector(page=page, selector=selector)
                action = {
                    "step": "bootstrap_click",
                    "provider": provider,
                    "attempt": attempt_index,
                    "selector": selector,
                    "clicked": clicked,
                }
                if not clicked:
                    self._bootstrap_actions.append(action)
                    continue
                ready = await self._wait_for_form_visible(
                    page=page,
                    timeout_ms=max(self.action_timeout_ms * 2, 1800),
                )
                action["ready_after_click"] = ready
                self._bootstrap_actions.append(action)
                if ready:
                    self._bootstrap_outcome = "visible_after_click"
                    return True
            ready_without_click = await self._has_visible_form_indicator(page=page)
            self._bootstrap_actions.append(
                {
                    "step": "bootstrap_poll",
                    "provider": provider,
                    "attempt": attempt_index,
                    "ready": ready_without_click,
                }
            )
            if ready_without_click:
                self._bootstrap_outcome = "visible_after_poll"
                return True

        self._bootstrap_outcome = "form_not_visible_pre_fill"
        return False

    async def _fill_application_form(self, *, page: Any, request: ApplyRunRequest) -> dict[str, Any]:
        values = self._build_fill_values(request)
        self._active_request = request
        self._current_values = values
        filled_count = 0
        stage_diagnostics: list[FormFillDiagnostics] = []
        stage_snapshots: list[dict[str, Any]] = []

        async def _capture_stage(stage_name: str) -> list[str]:
            snapshot = await capture_form_snapshot(page, max_fields=300)
            unresolved = await self._audit_required_fields(page)
            grouped = group_fields_by_semantic_key(list(snapshot.fields))
            stage_snapshots.append(
                {
                    "stage": stage_name,
                    "captured_at": snapshot.captured_at,
                    "field_group_count": len(grouped),
                    "fields": self._snapshot_field_trace_rows(snapshot=snapshot),
                }
            )
            stage_diagnostics.append(
                FormFillDiagnostics(
                    run_stage=stage_name,
                    detected_field_count=len(snapshot.fields),
                    filled_count=filled_count,
                    unresolved_required=tuple(unresolved),
                    attempts=tuple(self._field_attempt_traces[-40:]),
                )
            )
            return unresolved

        # Pass A: stable profile fields.
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name*='first'][name*='name']",
                    "input[id*='first'][id*='name']",
                    "#first_name",
                    "input[autocomplete='given-name']",
                ],
                value=values["first_name"],
                field_label="first_name",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name*='last'][name*='name']",
                    "input[id*='last'][id*='name']",
                    "#last_name",
                    "input[autocomplete='family-name']",
                ],
                value=values["last_name"],
                field_label="last_name",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name='name']",
                    "input[name*='full'][name*='name']",
                    "input[id*='full'][id*='name']",
                    "input[autocomplete='name']",
                ],
                value=values["full_name"],
                field_label="full_name",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[type='email']",
                    "input[name*='email']",
                    "input[id*='email']",
                    "#email",
                    "input[autocomplete='email']",
                ],
                value=values["email"],
                field_label="email",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[type='tel']",
                    "input[name*='phone']",
                    "input[id*='phone']",
                    "#phone",
                    "input[autocomplete='tel']",
                ],
                value=values["phone"],
                field_label="phone",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name*='city']",
                    "input[id*='city']",
                    "#candidate-location",
                    "input[autocomplete='address-level2']",
                ],
                value=values["city"],
                field_label="city",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name='state']",
                    "input[name*='state']",
                    "input[autocomplete='address-level1']",
                ],
                value=values["state"],
                field_label="state",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=["input[name*='country']", "input[id*='country']"],
                value=values["country"],
                field_label="country",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=["input[name*='linkedin']", "input[id*='linkedin']"],
                value=values["linkedin"],
                field_label="linkedin",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=["input[name*='github']", "input[id*='github']"],
                value=values["github"],
                field_label="github",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name*='portfolio']",
                    "input[id*='portfolio']",
                    "input[name*='website']",
                ],
                value=values["portfolio"],
                field_label="portfolio",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=[
                    "input[name*='work_authorization']",
                    "select[name*='work_authorization']",
                ],
                value=values["work_authorization"],
                field_label="work_authorization",
            )
        )
        filled_count += int(
            await self._fill_boolean_field(
                page,
                selectors=[
                    "input[name*='sponsor']",
                    "select[name*='sponsor']",
                    "input[name*='requires_sponsorship']",
                ],
                value=bool(values["requires_sponsorship"]),
                field_label="requires_sponsorship",
            )
        )
        filled_count += int(
            await self._fill_boolean_field(
                page,
                selectors=["input[name*='relocate']", "select[name*='relocate']"],
                value=bool(values["willing_to_relocate"]),
                field_label="willing_to_relocate",
            )
        )
        filled_count += int(
            await self._fill_text_field(
                page,
                selectors=["input[name*='experience']", "input[id*='experience']"],
                value=values["years_experience"],
                field_label="years_experience",
            )
        )
        if await self._upload_resume_file(page=page, request=request):
            filled_count += 1
        if await self._upload_cover_letter_file(page=page, request=request):
            filled_count += 1

        unresolved_required = await _capture_stage("pass_a_top_level")

        # Pass B/C: dynamic required + enrichments.
        filled_count += await self._fill_greenhouse_question_fields(
            page=page,
            request=request,
            values=values,
        )
        filled_count += await self._fill_generic_required_fields(
            page=page,
            request=request,
            values=values,
        )
        if not self.dev_review_mode:
            filled_count += int(
                await self._fill_text_field(
                    page,
                    selectors=[
                        "textarea[name*='cover']",
                        "textarea[id*='cover']",
                        "textarea[name*='letter']",
                        "#cover_letter",
                    ],
                    value=values["cover_letter"],
                    field_label="cover_letter",
                )
            )
        unresolved_required = await _capture_stage("pass_b_dynamic_required")

        if not self.form_engine_v2_enabled:
            logger.info(
                "playwright_form_fill_summary",
                extra={
                    "engine_version": "legacy",
                    "filled_fields": filled_count,
                    "unresolved_required_count": len(unresolved_required),
                    "dev_review_mode": self.dev_review_mode,
                },
            )
            diagnostics = {
                "engine_version": "legacy",
                "filled_fields": filled_count,
                "unresolved_required": unresolved_required,
                "unresolved_summary": self._build_unresolved_summary(unresolved_required),
                "llm_calls_used": self._llm_calls_used,
                "llm_call_budget": self.form_llm_max_calls,
                "combobox_trace": self._combobox_trace[-80:],
                "required_audit_stats": self._required_audit_stats,
                "bootstrap_actions": self._bootstrap_actions,
                "bootstrap_outcome": self._bootstrap_outcome,
                "field_snapshots": stage_snapshots,
                "stages": [diagnostics_to_dict(item) for item in stage_diagnostics],
            }
            return diagnostics

        # Pass D: retry unresolved required fields with rescans.
        for retry_index in range(self.form_max_field_retries):
            if not unresolved_required:
                break
            retry_before = filled_count
            if any("resume" in normalize_text(item) or "cv" in normalize_text(item) for item in unresolved_required):
                if await self._upload_resume_file(page=page, request=request):
                    filled_count += 1
            if any(
                "cover" in normalize_text(item) and "letter" in normalize_text(item)
                for item in unresolved_required
            ):
                if await self._upload_cover_letter_file(page=page, request=request):
                    filled_count += 1
            filled_count += await self._fill_greenhouse_question_fields(
                page=page,
                request=request,
                values=values,
            )
            filled_count += await self._fill_generic_required_fields(
                page=page,
                request=request,
                values=values,
            )
            unresolved_required = await _capture_stage(f"pass_d_retry_{retry_index + 1}")
            logger.info(
                "playwright_form_retry",
                extra={
                    "retry_index": retry_index + 1,
                    "unresolved_required_count": len(unresolved_required),
                    "filled_delta": filled_count - retry_before,
                },
            )
            if filled_count <= retry_before and retry_index >= 1:
                break

        logger.info(
            "playwright_form_fill_summary",
            extra={
                "engine_version": "v2" if self.form_engine_v2_enabled else "legacy",
                "filled_fields": filled_count,
                "unresolved_required_count": len(unresolved_required),
                "dev_review_mode": self.dev_review_mode,
            },
        )
        if unresolved_required:
            logger.warning(
                "playwright_required_fields_unresolved",
                extra={
                    "count": len(unresolved_required),
                    "fields": unresolved_required[:8],
                },
            )

        diagnostics = {
            "engine_version": "v2" if self.form_engine_v2_enabled else "legacy",
            "filled_fields": filled_count,
            "unresolved_required": unresolved_required,
            "unresolved_summary": self._build_unresolved_summary(unresolved_required),
            "llm_calls_used": self._llm_calls_used,
            "llm_call_budget": self.form_llm_max_calls,
            "combobox_trace": self._combobox_trace[-80:],
            "required_audit_stats": self._required_audit_stats,
            "bootstrap_actions": self._bootstrap_actions,
            "bootstrap_outcome": self._bootstrap_outcome,
            "field_snapshots": stage_snapshots,
            "stages": [diagnostics_to_dict(item) for item in stage_diagnostics],
        }
        return diagnostics

    def _build_unresolved_summary(self, unresolved: list[str]) -> dict[str, int]:
        summary: dict[str, int] = defaultdict(int)
        for item in unresolved:
            text = normalize_text(item)
            if not text:
                continue
            if "cover" in text and "letter" in text:
                summary["missing_cover_letter_file"] += 1
            elif "resume" in text or "cv" in text:
                summary["missing_resume_upload"] += 1
            elif "authorization" in text or "authorized" in text:
                summary["work_authorization_unresolved"] += 1
            elif "select" in text or "dropdown" in text:
                summary["no_valid_option_match"] += 1
            else:
                summary["other"] += 1
        for attempt in self._field_attempt_traces:
            if attempt.success:
                continue
            category = normalize_text(attempt.failure_category or "")
            if not category:
                continue
            summary[category] += 1
        if self._required_audit_stats:
            for category, count in self._required_audit_stats.items():
                if count > 0:
                    summary[normalize_text(category)] += int(count)
        return dict(summary)

    def _is_field_circuit_open(self, semantic_key: str) -> bool:
        normalized_key = normalize_key(semantic_key)
        if not normalized_key:
            return False
        for (field_key, _failure_category), count in self._field_failure_repeats.items():
            if field_key == normalized_key and int(count) >= 2:
                return True
        return False

    async def _fill_boolean_field(
        self,
        page: Any,
        *,
        selectors: list[str],
        value: bool,
        field_label: str | None = None,
    ) -> bool:
        normalized = "yes" if value else "no"
        return await self._fill_text_field(
            page,
            selectors=selectors,
            value=normalized,
            field_label=field_label,
        )

    async def _fill_text_field(
        self,
        page: Any,
        *,
        selectors: list[str],
        value: Any,
        field_label: str | None = None,
    ) -> bool:
        text = str(value).strip() if value is not None else ""
        if not text:
            return False

        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
            except Exception:
                continue
            for index in range(min(count, 4)):
                candidate = locator.nth(index)
                try:
                    if not await candidate.is_visible(timeout=200):
                        continue
                    if await self._fill_locator_candidate(
                        page=page,
                        candidate=candidate,
                        value=text,
                        field_label=field_label or selector,
                    ):
                        return True
                except Exception:
                    continue
        return False

    async def _fill_locator_candidate(
        self,
        *,
        page: Any,
        candidate: Any,
        value: str,
        field_label: str | None = None,
    ) -> bool:
        text = str(value).strip()
        if not text:
            return False
        metadata: dict[str, Any] = {
            "tag_name": "",
            "input_type": "",
            "role": "",
            "label": field_label or "",
            "name": "",
            "field_id": "",
            "helper_text": "",
            "placeholder": "",
            "options": [],
        }
        try:
            metadata = await candidate.evaluate(
                """el => {
                    const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                    const findLabel = () => {
                        const byFor = el.id
                            ? document.querySelector(`label[for="${el.id.replace(/"/g, '\\"')}"]`)
                            : null;
                        if (byFor) return normalize(byFor.textContent);
                        const wrapping = el.closest("label");
                        if (wrapping) return normalize(wrapping.textContent);
                        return normalize(el.getAttribute("aria-label"));
                    };
                    const helperText = () => {
                        const describedBy = normalize(el.getAttribute("aria-describedby"));
                        if (!describedBy) return "";
                        const ids = describedBy.split(" ").map((part) => part.trim()).filter(Boolean);
                        const chunks = [];
                        for (const id of ids) {
                            const node = document.getElementById(id);
                            if (node) chunks.push(normalize(node.textContent));
                        }
                        return normalize(chunks.join(" "));
                    };
                    const options = [];
                    if (String(el.tagName || "").toLowerCase() === "select") {
                        for (const option of Array.from(el.options || [])) {
                            options.push({
                                label: normalize(option.label || option.textContent),
                                value: normalize(option.value),
                                selected: Boolean(option.selected),
                            });
                        }
                    }
                    return {
                        tag_name: String(el.tagName || "").toLowerCase(),
                        input_type: normalize(el.getAttribute("type")).toLowerCase(),
                        role: normalize(el.getAttribute("role")).toLowerCase(),
                        label: findLabel(),
                        name: normalize(el.getAttribute("name")),
                        field_id: normalize(el.id),
                        helper_text: helperText(),
                        placeholder: normalize(el.getAttribute("placeholder")),
                        options,
                    };
                }"""
            )
        except Exception:
            pass
        if not isinstance(metadata, dict):
            metadata = {
                "tag_name": "",
                "input_type": "",
                "role": "",
                "label": field_label or "",
                "name": "",
                "field_id": "",
                "helper_text": "",
                "placeholder": "",
                "options": [],
            }
        tag_name = str(metadata.get("tag_name", "")).strip().lower()
        input_type = str(metadata.get("input_type", "")).strip().lower()
        role = str(metadata.get("role", "")).strip().lower()
        helper_text = str(metadata.get("helper_text", "")).strip()
        resolved_label = (
            str(metadata.get("label", "")).strip()
            or str(metadata.get("name", "")).strip()
            or str(metadata.get("field_id", "")).strip()
            or str(field_label or "").strip()
            or "unknown"
        )
        semantic_key = (
            str(metadata.get("name", "")).strip()
            or str(metadata.get("field_id", "")).strip()
            or resolved_label
        )
        option_labels = [
            str(item.get("label", "")).strip()
            for item in (metadata.get("options") or [])
            if isinstance(item, dict) and str(item.get("label", "")).strip()
        ]
        intent = classify_field_intent(
            label=resolved_label,
            name=str(metadata.get("name", "")).strip(),
            field_id=str(metadata.get("field_id", "")).strip(),
            helper_text=helper_text,
            tag_name=tag_name,
            input_type=input_type,
            role=role,
            options=option_labels,
        )
        widget_type = classify_widget_type(
            tag_name=tag_name,
            input_type=input_type,
            role=role,
        )
        option_only = is_option_constrained(
            tag_name=tag_name,
            input_type=input_type,
            role=role,
        )
        placeholder_hint = normalize_text(str(metadata.get("placeholder", "")).strip())
        if (not option_only) and input_type in {"text", ""} and "select" in placeholder_hint:
            option_only = True
        constraint_mode = "option_only" if option_only else "free_text"
        file_slot = classify_file_slot(
            label=resolved_label,
            name=str(metadata.get("name", "")).strip(),
            field_id=str(metadata.get("field_id", "")).strip(),
            helper_text=helper_text,
        )

        if input_type == "file":
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="file_input",
                attempted_value=text,
                success=False,
                verified=False,
                reason="file_input_skipped",
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category="file_input_skipped",
                file_slot=file_slot,
            )
            return False

        if input_type in {"checkbox", "radio"}:
            normalized = text.lower()
            should_check = normalized in {"yes", "true", "1", "agree", "i agree"}
            success = await self._set_checkbox_or_radio(candidate=candidate, value=should_check)
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="radio_checkbox",
                attempted_value=text,
                success=success,
                verified=success,
                reason=None if success else "unable_to_toggle",
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category=None if success else "no_valid_option_match",
                file_slot=file_slot,
            )
            return success

        if tag_name == "select":
            success = await self._fill_select_field(
                candidate=candidate,
                request_label=resolved_label,
                request_name=str(metadata.get("name", "")),
                request_field_id=str(metadata.get("field_id", "")),
                preferred_value=text,
                options=metadata.get("options", []),
            )
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="native_select",
                attempted_value=text,
                success=success,
                verified=success,
                reason=None if success else "select_option_failed",
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category=None if success else "no_valid_option_match",
                file_slot=file_slot,
            )
            return success

        if role in {"combobox", "listbox"}:
            success = await self._fill_combobox(
                page=page,
                candidate=candidate,
                value=text,
                field_label=resolved_label,
                field_name=str(metadata.get("name", "")).strip(),
                field_id=str(metadata.get("field_id", "")).strip(),
            )
            failure_category = self._last_combobox_failure_category or "no_valid_option_match"
            failure_reason = "combobox_selection_failed"
            if isinstance(self._last_combobox_debug, dict):
                failure_reason = str(
                    self._last_combobox_debug.get("reason", failure_reason)
                )
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="combobox" if role == "combobox" else "listbox",
                attempted_value=text,
                success=success,
                verified=success,
                reason=None if success else failure_reason,
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category=None if success else failure_category,
                file_slot=file_slot,
            )
            return success

        if tag_name == "textarea":
            with suppress(Exception):
                await candidate.fill(text, timeout=self.action_timeout_ms)
                verified = await self._verify_candidate_filled(
                    candidate=candidate,
                    expected=text,
                )
                self._record_field_attempt(
                    semantic_key=semantic_key,
                    label=resolved_label,
                    adapter="textarea",
                    attempted_value=text,
                    success=verified,
                    verified=verified,
                    reason=None if verified else "textarea_verification_failed",
                    intent=intent,
                    widget_type=widget_type,
                    constraint_mode=constraint_mode,
                    failure_category=None if verified else "verification_failed",
                    file_slot=file_slot,
                )
                return verified

        if tag_name in {"", "input"} and input_type in {"text", "email", "tel", "number", "url", "search", ""}:
            if option_only:
                self._record_field_attempt(
                    semantic_key=semantic_key,
                    label=resolved_label,
                    adapter="text_input",
                    attempted_value=text,
                    success=False,
                    verified=False,
                    reason="option_constrained_field",
                    intent=intent,
                    widget_type=widget_type,
                    constraint_mode=constraint_mode,
                    failure_category="no_valid_option_match",
                    file_slot=file_slot,
                )
                return False
            with suppress(Exception):
                await candidate.fill(text, timeout=self.action_timeout_ms)
                verified = await self._verify_candidate_filled(candidate=candidate, expected=text)
                self._record_field_attempt(
                    semantic_key=semantic_key,
                    label=resolved_label,
                    adapter="text_input",
                    attempted_value=text,
                    success=verified,
                    verified=verified,
                    reason=None if verified else "text_verification_failed",
                    intent=intent,
                    widget_type=widget_type,
                    constraint_mode=constraint_mode,
                    failure_category=None if verified else "verification_failed",
                    file_slot=file_slot,
                )
                return verified

        if option_only:
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="option_only_guard",
                attempted_value=text,
                success=False,
                verified=False,
                reason="option_constrained_field",
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category="no_valid_option_match",
                file_slot=file_slot,
            )
            return False

        with suppress(Exception):
            await candidate.fill(text, timeout=self.action_timeout_ms)
            verified = await self._verify_candidate_filled(candidate=candidate, expected=text)
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="fallback_fill",
                attempted_value=text,
                success=verified,
                verified=verified,
                reason=None if verified else "fallback_verification_failed",
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category=None if verified else "verification_failed",
                file_slot=file_slot,
            )
            return verified
        with suppress(Exception):
            await candidate.evaluate(
                """(el, value) => {
                    if (el.isContentEditable) {
                        el.innerText = value;
                        el.dispatchEvent(new Event("input", { bubbles: true }));
                        el.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                }""",
                text,
            )
            verified = await self._verify_candidate_filled(candidate=candidate, expected=text)
            self._record_field_attempt(
                semantic_key=semantic_key,
                label=resolved_label,
                adapter="contenteditable",
                attempted_value=text,
                success=verified,
                verified=verified,
                reason=None if verified else "contenteditable_verification_failed",
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category=None if verified else "verification_failed",
                file_slot=file_slot,
            )
            return verified
        self._record_field_attempt(
            semantic_key=semantic_key,
            label=resolved_label,
            adapter="unknown",
            attempted_value=text,
            success=False,
            verified=False,
            reason="unsupported_widget",
            intent=intent,
            widget_type=widget_type,
            constraint_mode=constraint_mode,
            failure_category="unsupported_widget",
            file_slot=file_slot,
        )
        return False

    async def _fill_combobox(
        self,
        *,
        page: Any,
        candidate: Any,
        value: str,
        field_label: str,
        field_name: str = "",
        field_id: str = "",
    ) -> bool:
        self._last_combobox_failure_category = None
        self._last_combobox_debug = None
        debug_attempts: list[dict[str, Any]] = []
        typed_probe_cleared = False
        max_attempts = 3
        normalized_value = str(value or "").strip()

        for attempt_index in range(1, max_attempts + 1):
            with suppress(Exception):
                await candidate.click(timeout=self.action_timeout_ms)

            discovered = await self._discover_combobox_options(page=page, candidate=candidate)
            options = discovered.get("options", [])
            discovered_selector = str(discovered.get("selector", ""))
            discovered_reason = str(discovered.get("reason", ""))
            discovered_scope = str(discovered.get("scope", ""))
            probe_used = ""

            if not options and normalized_value:
                probe_used = normalized_value
                with suppress(Exception):
                    await candidate.fill("", timeout=min(self.action_timeout_ms, 1000))
                    await candidate.type(normalized_value, delay=10)
                await asyncio.sleep(0.2)
                discovered = await self._discover_combobox_options(page=page, candidate=candidate)
                options = discovered.get("options", [])
                discovered_selector = str(discovered.get("selector", ""))
                discovered_reason = str(discovered.get("reason", ""))
                discovered_scope = str(discovered.get("scope", ""))

            if not options:
                typed_probe_cleared = await self._clear_combobox_probe(candidate=candidate) or typed_probe_cleared
                debug_attempts.append(
                    {
                        "attempt": attempt_index,
                        "options": [],
                        "selector": discovered_selector,
                        "active_dropdown_selector": discovered_selector,
                        "scope": discovered_scope,
                        "reason": discovered_reason or "no_options_discovered",
                        "probe_value": probe_used,
                        "typed_probe_cleared": typed_probe_cleared,
                    }
                )
                self._last_combobox_failure_category = "combobox_options_not_discovered"
                continue

            selected_value = self._choose_option_value(
                label=field_label,
                name=field_name or field_label,
                field_id=field_id or field_label,
                options=options,
                preferred_value=normalized_value,
                allow_llm_fallback=True,
            ) or best_option_match(normalized_value, options)

            if not selected_value:
                typed_probe_cleared = await self._clear_combobox_probe(candidate=candidate) or typed_probe_cleared
                debug_attempts.append(
                    {
                        "attempt": attempt_index,
                        "options": options[:20],
                        "selector": discovered_selector,
                        "active_dropdown_selector": discovered_selector,
                        "scope": discovered_scope,
                        "reason": "no_valid_option_match",
                        "probe_value": probe_used,
                        "typed_probe_cleared": typed_probe_cleared,
                    }
                )
                self._last_combobox_failure_category = "no_valid_option_match"
                continue

            clicked = await self._click_combobox_option_scoped(
                page=page,
                candidate=candidate,
                selected_value=selected_value,
                active_dropdown_selector=discovered_selector,
            )
            if not clicked:
                debug_attempts.append(
                    {
                        "attempt": attempt_index,
                        "options": options[:20],
                        "selector": discovered_selector,
                        "active_dropdown_selector": discovered_selector,
                        "scope": discovered_scope,
                        "reason": "option_not_clickable",
                        "probe_value": probe_used,
                        "selected_value": selected_value,
                    }
                )
                self._last_combobox_failure_category = "combobox_option_not_clickable"
                continue

            await asyncio.sleep(0.15)
            verified = await self._verify_candidate_matches_option(
                candidate=candidate,
                selected_value=selected_value,
            )
            if verified:
                self._last_combobox_debug = {
                    "reason": "selected",
                    "selected_value": selected_value,
                    "attempts": debug_attempts,
                    "typed_probe_cleared": typed_probe_cleared,
                    "options_snapshot": options[:20],
                    "dropdown_selector": discovered_selector,
                    "discovery_scope": discovered_scope,
                }
                self._combobox_trace.append(
                    {
                        "field_label": field_label,
                        "field_name": field_name,
                        "field_id": field_id,
                        **self._last_combobox_debug,
                    }
                )
                return True

            debug_attempts.append(
                {
                    "attempt": attempt_index,
                    "options": options[:20],
                    "selector": discovered_selector,
                    "active_dropdown_selector": discovered_selector,
                    "scope": discovered_scope,
                    "reason": "verification_failed",
                    "probe_value": probe_used,
                    "selected_value": selected_value,
                }
            )
            self._last_combobox_failure_category = "combobox_option_not_clickable"

        typed_probe_cleared = await self._clear_combobox_probe(candidate=candidate) or typed_probe_cleared
        self._last_combobox_debug = {
            "reason": self._last_combobox_failure_category or "combobox_selection_failed",
            "attempts": debug_attempts,
            "typed_probe_cleared": typed_probe_cleared,
            "dropdown_selector": "",
            "discovery_scope": "",
        }
        self._combobox_trace.append(
            {
                "field_label": field_label,
                "field_name": field_name,
                "field_id": field_id,
                **self._last_combobox_debug,
            }
        )
        return False

    async def _clear_combobox_probe(self, *, candidate: Any) -> bool:
        with suppress(Exception):
            await candidate.fill("", timeout=min(self.action_timeout_ms, 1000))
            return True
        with suppress(Exception):
            await candidate.evaluate(
                """el => {
                    if (el && typeof el.value !== "undefined") {
                        el.value = "";
                        el.dispatchEvent(new Event("input", { bubbles: true }));
                        el.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                }"""
            )
            return True
        return False

    async def _discover_combobox_options(
        self,
        *,
        page: Any,
        candidate: Any,
    ) -> dict[str, Any]:
        options: list[str] = []
        selector = ""
        reason = ""
        scope = ""
        with suppress(Exception):
            data = await candidate.evaluate(
                """el => {
                    const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                    const isVisible = (node) => {
                        if (!node) return false;
                        const style = window.getComputedStyle(node);
                        if (style.visibility === "hidden" || style.display === "none") return false;
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const selectors = [
                        "[role='option']",
                        ".select__option",
                        ".select-option",
                        ".option",
                        "[data-option-index]",
                        "li[role='option']",
                        "[id*='-option-']"
                    ];
                    const seen = new Set();
                    const gather = (root) => {
                        const rows = [];
                        if (!root) return rows;
                        for (const selector of selectors) {
                            const nodes = Array.from(root.querySelectorAll(selector));
                            for (const node of nodes) {
                                if (!isVisible(node)) continue;
                                const text = normalize(node.textContent || node.getAttribute("data-value") || "");
                                if (!text || seen.has(text.toLowerCase())) continue;
                                seen.add(text.toLowerCase());
                                rows.push(text);
                            }
                        }
                        return rows;
                    };

                    let container = null;
                    let discoveryScope = "";
                    const controls = normalize(el.getAttribute("aria-controls"));
                    if (controls) {
                        const controlled = document.getElementById(controls);
                        if (controlled && isVisible(controlled)) {
                            container = controlled;
                            discoveryScope = "aria_controls";
                        }
                    }
                    if (!container) {
                        const nearby = el.closest("[class*='field'], .application-field, .field, [data-testid*='question']");
                        if (nearby) {
                            const localMenu = nearby.querySelector(
                                "[role='listbox'], .select__menu, .select-menu, [id*='-listbox'], [id*='-menu']"
                            );
                            if (localMenu && isVisible(localMenu)) {
                                container = localMenu;
                                discoveryScope = "nearby_field";
                            }
                        }
                    }
                    if (!container) {
                        const globalMenus = Array.from(
                            document.querySelectorAll("[role='listbox'], .select__menu, .select-menu, [id*='-listbox'], [id*='-menu']")
                        );
                        const globalMenu = globalMenus.find((node) => isVisible(node)) || null;
                        if (globalMenu && isVisible(globalMenu)) {
                            container = globalMenu;
                            discoveryScope = "global_listbox";
                        }
                    }

                    const rows = gather(container).slice(0, 30);
                    const selectedSelector =
                        container
                            ? (container.getAttribute("id") ? `#${container.getAttribute("id")}` : String(container.className || "").split(" ").map((part) => part.trim()).filter(Boolean).map((part) => `.${part}`).join(""))
                            : "";
                    return {
                        options: rows,
                        selector: selectedSelector,
                        scope: discoveryScope,
                        reason: rows.length ? "options_found" : "no_options_found",
                    };
                }"""
            )
            if isinstance(data, dict):
                options_raw = data.get("options")
                if isinstance(options_raw, list):
                    options = [str(item).strip() for item in options_raw if str(item).strip()]
                selector = str(data.get("selector", "")).strip()
                scope = str(data.get("scope", "")).strip()
                reason = str(data.get("reason", "")).strip()
        return {"options": options, "selector": selector, "reason": reason, "scope": scope}

    async def _click_combobox_option_scoped(
        self,
        *,
        page: Any,
        candidate: Any,
        selected_value: str,
        active_dropdown_selector: str = "",
    ) -> bool:
        option_selectors = (
            "[role='option']",
            ".select__option",
            ".select-option",
            ".option",
            "[data-option-index]",
            "li[role='option']",
            "[id*='-option-']",
        )
        target_norm = normalize_text(selected_value)
        if hasattr(page, "locator"):
            scoped_roots = [active_dropdown_selector] if active_dropdown_selector else []
            if not scoped_roots:
                scoped_roots = ["[role='listbox']", ".select__menu", ".select-menu"]
            for root in scoped_roots:
                for option_selector in option_selectors:
                    selector = f"{root} {option_selector}".strip()
                    locator = page.locator(selector)
                    count = 0
                    with suppress(Exception):
                        count = await locator.count()
                    for index in range(min(count, 30)):
                        option = locator.nth(index)
                        with suppress(Exception):
                            if not await option.is_visible(timeout=200):
                                continue
                        option_text = ""
                        with suppress(Exception):
                            option_text = await option.evaluate(
                                """el => String(el.textContent || el.getAttribute("data-value") || "").replace(/\\s+/g, " ").trim()"""
                            )
                        if not option_text:
                            continue
                        normalized_option = normalize_text(str(option_text))
                        if normalized_option != target_norm and target_norm not in normalized_option:
                            continue
                        with suppress(Exception):
                            await option.click(timeout=min(self.action_timeout_ms, 1200))
                            return True

        with suppress(Exception):
            clicked = await candidate.evaluate(
                """(el, payload) => {
                    const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                    const isVisible = (node) => {
                        if (!node) return false;
                        const style = window.getComputedStyle(node);
                        if (style.visibility === "hidden" || style.display === "none") return false;
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const targetNorm = normalize(payload && payload.target || "");
                    const dropdownSelector = String((payload && payload.selector) || "").trim();
                    const controls = String(el.getAttribute("aria-controls") || "").trim();
                    const containers = [];
                    if (dropdownSelector) {
                        try {
                            const scoped = document.querySelector(dropdownSelector);
                            if (scoped && isVisible(scoped)) containers.push(scoped);
                        } catch (_err) {}
                    }
                    if (controls) {
                        const controlled = document.getElementById(controls);
                        if (controlled && isVisible(controlled)) containers.push(controlled);
                    }
                    const nearby = el.closest("[class*='field'], .application-field, .field, [data-testid*='question']");
                    if (nearby) {
                        for (const node of Array.from(nearby.querySelectorAll("[role='listbox'], .select__menu, .select-menu, [id*='menu']"))) {
                            if (isVisible(node)) containers.push(node);
                        }
                    }
                    for (const node of Array.from(document.querySelectorAll("[role='listbox'], .select__menu, .select-menu"))) {
                        if (isVisible(node)) containers.push(node);
                    }

                    const optionSelectors = [
                        "[role='option']",
                        ".select__option",
                        ".select-option",
                        ".option",
                        "[data-option-index]",
                        "li[role='option']",
                        "[id*='-option-']"
                    ];
                    const seen = new Set();
                    const candidates = [];
                    for (const container of containers) {
                        for (const selector of optionSelectors) {
                            for (const option of Array.from(container.querySelectorAll(selector))) {
                                if (!isVisible(option)) continue;
                                if (seen.has(option)) continue;
                                seen.add(option);
                                const text = String(option.textContent || option.getAttribute("data-value") || "").replace(/\\s+/g, " ").trim();
                                if (!text) continue;
                                candidates.push({ option, text });
                            }
                        }
                    }
                    const exact = candidates.find((row) => normalize(row.text) === targetNorm);
                    if (exact) {
                        for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                            exact.option.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
                        }
                        return true;
                    }
                    const partial = candidates.find((row) => normalize(row.text).includes(targetNorm));
                    if (partial) {
                        for (const eventName of ["pointerdown", "mousedown", "mouseup", "click"]) {
                            partial.option.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
                        }
                        return true;
                    }
                    return false;
                }""",
                {"target": selected_value, "selector": active_dropdown_selector},
            )
            if bool(clicked):
                return True
        with suppress(Exception):
            option = page.get_by_role(
                "option",
                name=re.compile(re.escape(selected_value), re.IGNORECASE),
            ).first
            await option.click(timeout=min(self.action_timeout_ms, 1200))
            return True
        return False

    async def _fill_select_field(
        self,
        *,
        candidate: Any,
        request_label: str,
        request_name: str,
        request_field_id: str,
        preferred_value: str,
        options: Any,
    ) -> bool:
        option_rows = options if isinstance(options, list) else []
        options_text: list[str] = []
        options_map: dict[str, dict[str, Any]] = {}
        for row in option_rows:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label", "")).strip()
            value = str(row.get("value", "")).strip()
            if label:
                options_text.append(label)
                options_map[normalize_text(label)] = row
            if value:
                options_map.setdefault(normalize_text(value), row)

        normalized_label = normalize_text(request_label)
        preferred = preferred_value
        if (
            "authorized" in normalized_label
            or "work authorization" in normalized_label
            or "eligible to work" in normalized_label
        ):
            preferred = (
                "No" if bool(self._current_values.get("requires_sponsorship")) else "Yes"
            )
        selected_option: str | None = None
        if self._active_request is not None:
            selected_option = self._choose_option_value(
                label=request_label,
                name=request_name,
                field_id=request_field_id,
                options=options_text,
                preferred_value=preferred,
                allow_llm_fallback=True,
            )
        selected_option = selected_option or best_option_match(preferred, options_text)
        if not selected_option:
            selected_option = best_option_match(preferred_value, options_text)
        if not selected_option:
            return False

        candidate_row = options_map.get(normalize_text(selected_option))
        if candidate_row:
            with suppress(Exception):
                label = str(candidate_row.get("label", "")).strip()
                if label:
                    await candidate.select_option(label=label)
                    return await self._verify_select_matches(
                        candidate=candidate,
                        selected_option=selected_option,
                    )
            with suppress(Exception):
                value = str(candidate_row.get("value", "")).strip()
                if value:
                    await candidate.select_option(value=value)
                    return await self._verify_select_matches(
                        candidate=candidate,
                        selected_option=selected_option,
                    )

        with suppress(Exception):
            await candidate.select_option(label=selected_option)
            return await self._verify_select_matches(
                candidate=candidate,
                selected_option=selected_option,
            )
        with suppress(Exception):
            await candidate.select_option(value=selected_option)
            return await self._verify_select_matches(
                candidate=candidate,
                selected_option=selected_option,
            )
        return False

    async def _verify_candidate_filled(self, *, candidate: Any, expected: str) -> bool:
        with suppress(Exception):
            value = await candidate.input_value()
            normalized_value = normalize_text(value)
            if normalized_value and not is_placeholder_value(normalized_value):
                return True
        with suppress(Exception):
            value = await candidate.evaluate(
                "el => String(el.value || el.textContent || '').trim()"
            )
            normalized_value = normalize_text(str(value))
            if normalized_value and not is_placeholder_value(normalized_value):
                return True
        normalized_expected = normalize_text(expected)
        if not normalized_expected:
            return False
        with suppress(Exception):
            value = await candidate.evaluate(
                "el => String(el.value || el.textContent || '').trim()"
            )
            return normalize_text(str(value)) == normalized_expected
        return False

    async def _verify_select_not_placeholder(self, candidate: Any) -> bool:
        with suppress(Exception):
            value = await candidate.evaluate(
                """el => {
                    const selected = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    const selectedLabel = selected ? String(selected.label || selected.textContent || "").trim() : "";
                    const selectedValue = selected ? String(selected.value || "").trim() : String(el.value || "").trim();
                    return { label: selectedLabel, value: selectedValue };
                }"""
            )
            if isinstance(value, dict):
                label = normalize_text(str(value.get("label", "")))
                selected_value = normalize_text(str(value.get("value", "")))
                return bool(
                    (label and not is_placeholder_value(label))
                    or (selected_value and not is_placeholder_value(selected_value))
                )
        return False

    async def _verify_select_matches(
        self,
        *,
        candidate: Any,
        selected_option: str,
    ) -> bool:
        selected_norm = normalize_text(selected_option)
        if not selected_norm:
            return False
        with suppress(Exception):
            selected = await candidate.evaluate(
                """el => {
                    const row = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
                    return {
                        label: row ? String(row.label || row.textContent || "").trim() : "",
                        value: row ? String(row.value || "").trim() : String(el.value || "").trim(),
                    };
                }"""
            )
            if isinstance(selected, dict):
                label_norm = normalize_text(str(selected.get("label", "")))
                value_norm = normalize_text(str(selected.get("value", "")))
                if label_norm == selected_norm or value_norm == selected_norm:
                    return True
        return await self._verify_select_not_placeholder(candidate)

    async def _verify_candidate_matches_option(
        self,
        *,
        candidate: Any,
        selected_value: str,
    ) -> bool:
        selected_norm = normalize_text(selected_value)
        if not selected_norm:
            return False
        for _attempt in range(3):
            with suppress(Exception):
                current_value = await candidate.input_value()
                normalized = normalize_text(current_value)
                if normalized == selected_norm:
                    return True
            with suppress(Exception):
                value = await candidate.evaluate(
                    "el => String(el.value || el.textContent || '').trim()"
                )
                normalized = normalize_text(str(value))
                if normalized == selected_norm:
                    return True
                if normalized and not is_placeholder_value(normalized):
                    return True
            with suppress(Exception):
                has_token = await candidate.evaluate(
                    """(el, target) => {
                        const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                        const targetNorm = normalize(target);
                        const container =
                            el.closest("[class*='field'], .application-field, .field, [data-testid*='question']")
                            || el.parentElement
                            || document.body;
                        const matches = (value) => {
                            const normalized = normalize(value);
                            return Boolean(
                                normalized &&
                                (
                                    normalized === targetNorm ||
                                    normalized.includes(targetNorm) ||
                                    targetNorm.includes(normalized)
                                )
                            );
                        };
                        const own = normalize(el.value || el.textContent || "");
                        if (matches(own)) return true;

                        const hiddenInputs = Array.from(container.querySelectorAll("input[type='hidden']"));
                        for (const input of hiddenInputs) {
                            if (matches(input.value || "")) return true;
                        }

                        const nodes = container.querySelectorAll(
                            ".select__single-value, [class*='singleValue'], .select__multi-value__label, [class*='multiValue'] [class*='label'], [class*='value-container'] [class*='singleValue'], .selected-value, .token, .chip"
                        );
                        for (const node of Array.from(nodes)) {
                            const text = normalize(node.textContent || "");
                            if (matches(text)) return true;
                        }
                        return false;
                    }""",
                    selected_value,
                )
                if bool(has_token):
                    return True
            await asyncio.sleep(0.18)
        return False

    async def _collect_visible_option_texts(self, page: Any) -> list[str]:
        if not hasattr(page, "evaluate"):
            return []
        with suppress(Exception):
            values = await page.evaluate(
                """() => {
                    const isVisible = (el) => {
                        const style = window.getComputedStyle(el);
                        if (style.visibility === "hidden" || style.display === "none") return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const selectors = [
                        "[role='option']",
                        "li[role='option']",
                        ".select__option",
                        ".select-option",
                        ".option",
                        "[data-option-index]",
                    ];
                    const rows = [];
                    const seen = new Set();
                    for (const selector of selectors) {
                        const options = Array.from(document.querySelectorAll(selector));
                        for (const el of options) {
                            if (!isVisible(el)) continue;
                            const text = String(el.textContent || "").replace(/\\s+/g, " ").trim();
                            if (!text) continue;
                            const key = text.toLowerCase();
                            if (seen.has(key)) continue;
                            seen.add(key);
                            rows.push(text);
                        }
                    }
                    return rows.slice(0, 30);
                }"""
            )
            if isinstance(values, list):
                return [str(item).strip() for item in values if str(item).strip()]
        return []

    def _record_field_attempt(
        self,
        *,
        semantic_key: str,
        label: str,
        adapter: str,
        attempted_value: str,
        success: bool,
        verified: bool,
        reason: str | None = None,
        intent: str | None = None,
        widget_type: str | None = None,
        constraint_mode: str | None = None,
        failure_category: str | None = None,
        file_slot: str | None = None,
    ) -> None:
        normalized_semantic = normalize_key(semantic_key)
        normalized_failure = normalize_key(failure_category or "unknown_failure")
        if success and normalized_semantic:
            for key in list(self._field_failure_repeats.keys()):
                if key[0] == normalized_semantic:
                    self._field_failure_repeats.pop(key, None)
        elif not success and normalized_semantic and normalized_failure:
            key = (normalized_semantic, normalized_failure)
            self._field_failure_repeats[key] = int(self._field_failure_repeats.get(key, 0)) + 1

        self._field_attempt_traces.append(
            FieldFillAttempt(
                semantic_key=semantic_key,
                label=label,
                adapter=adapter,
                attempted_value=attempted_value if attempted_value else "",
                success=bool(success),
                verified=bool(verified),
                reason=reason,
                intent=intent,
                widget_type=widget_type,
                constraint_mode=constraint_mode,
                failure_category=failure_category,
                file_slot=file_slot,
            )
        )

    async def _set_checkbox_or_radio(self, *, candidate: Any, value: bool) -> bool:
        try:
            currently_checked = await candidate.is_checked()
        except Exception:
            currently_checked = False
        if currently_checked == value:
            return True
        with suppress(Exception):
            await candidate.scroll_into_view_if_needed(timeout=min(self.action_timeout_ms, 1000))
        with suppress(Exception):
            if value:
                await candidate.check(timeout=self.action_timeout_ms)
            else:
                await candidate.uncheck(timeout=self.action_timeout_ms)
            with suppress(Exception):
                if await candidate.is_checked() == value:
                    return True
            return True
        with suppress(Exception):
            clicked_label = await candidate.evaluate(
                """(el, shouldCheck) => {
                    const id = String(el.id || "").trim();
                    const clickNode = (node) => {
                        if (!node) return false;
                        node.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, view: window }));
                        node.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
                        node.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
                        node.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
                        return true;
                    };
                    if (id) {
                        const byFor = document.querySelector(`label[for="${id.replace(/"/g, '\\"')}"]`);
                        if (byFor && clickNode(byFor)) return true;
                    }
                    const wrapping = el.closest("label");
                    if (wrapping && clickNode(wrapping)) return true;
                    if (el && typeof el.click === "function") {
                        el.click();
                        return true;
                    }
                    return false;
                }""",
                value,
            )
            if clicked_label:
                with suppress(Exception):
                    if await candidate.is_checked() == value:
                        return True
        with suppress(Exception):
            await candidate.click(timeout=self.action_timeout_ms)
            with suppress(Exception):
                if await candidate.is_checked() == value:
                    return True
            return True
        with suppress(Exception):
            changed = await candidate.evaluate(
                """(el, shouldCheck) => {
                    if (!el || typeof el.checked === "undefined") return false;
                    el.checked = Boolean(shouldCheck);
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                    return true;
                }""",
                value,
            )
            if changed:
                with suppress(Exception):
                    if await candidate.is_checked() == value:
                        return True
        return False

    @staticmethod
    def _file_payload_from_bundle(
        *,
        bundle: dict[str, Any],
        default_filename: str,
    ) -> dict[str, Any] | None:
        filename = str(bundle.get("filename", default_filename)).strip() or default_filename
        content_base64 = str(bundle.get("content_base64", "")).strip()
        if not content_base64:
            return None
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not content:
            return None
        mime_type = str(bundle.get("mime_type", "")).strip() or "application/octet-stream"
        return {"name": filename, "mimeType": mime_type, "buffer": content}

    async def _upload_resume_file(self, *, page: Any, request: ApplyRunRequest) -> bool:
        bundle = self._resume_file_payload(request)
        if bundle is None:
            return False
        return await self._upload_file_to_slot(
            page=page,
            file_bundle=bundle,
            slot="resume",
            default_filename="resume.txt",
        )

    async def _upload_cover_letter_file(self, *, page: Any, request: ApplyRunRequest) -> bool:
        bundle = self._cover_letter_file_payload(request)
        if bundle is None:
            return False
        return await self._upload_file_to_slot(
            page=page,
            file_bundle=bundle,
            slot="cover_letter",
            default_filename="cover_letter.txt",
        )

    async def _upload_file_to_slot(
        self,
        *,
        page: Any,
        file_bundle: dict[str, Any],
        slot: str,
        default_filename: str,
    ) -> bool:
        if not hasattr(page, "locator"):
            return False
        file_payload = self._file_payload_from_bundle(
            bundle=file_bundle,
            default_filename=default_filename,
        )
        if file_payload is None:
            logger.warning(
                "playwright_file_upload_failed",
                extra={"slot": slot, "reason": "invalid_payload"},
            )
            return False
        filename = str(file_payload.get("name", default_filename))

        candidates = await self._rank_file_input_candidates(page=page, preferred_slot=slot)
        for item in candidates:
            if item.get("slot") != slot:
                continue
            candidate = item.get("candidate")
            if candidate is None:
                continue
            with suppress(Exception):
                await candidate.set_input_files(file_payload, timeout=self.action_timeout_ms)
                if await self._verify_file_input_upload(
                    candidate=candidate,
                    expected_filename=filename,
                ):
                    self._record_field_attempt(
                        semantic_key=str(item.get("semantic_key", slot)),
                        label=str(item.get("label", slot)),
                        adapter="file_upload",
                        attempted_value=filename,
                        success=True,
                        verified=True,
                        reason=None,
                        intent="file_upload",
                        widget_type="file_upload",
                        constraint_mode="option_only",
                        failure_category=None,
                        file_slot=slot,
                    )
                    return True

        if await self._upload_file_via_file_chooser(
            page=page,
            file_payload=file_payload,
            slot=slot,
        ):
            self._record_field_attempt(
                semantic_key=slot,
                label=slot,
                adapter="file_upload_file_chooser",
                attempted_value=filename,
                success=True,
                verified=True,
                reason=None,
                intent="file_upload",
                widget_type="file_upload",
                constraint_mode="option_only",
                failure_category=None,
                file_slot=slot,
            )
            return True

        self._record_field_attempt(
            semantic_key=slot,
            label=slot,
            adapter="file_upload",
            attempted_value=filename,
            success=False,
            verified=False,
            reason="upload_not_applied",
            intent="file_upload",
            widget_type="file_upload",
            constraint_mode="option_only",
            failure_category="upload_not_applied",
            file_slot=slot,
        )
        return False

    async def _rank_file_input_candidates(
        self,
        *,
        page: Any,
        preferred_slot: str,
    ) -> list[dict[str, Any]]:
        ranked: list[dict[str, Any]] = []
        selector_candidates = [
            ("input[type='file']", 16),
            ("input[type='file']#resume", 6),
            ("input[type='file'][name*='resume']", 6),
            ("input[type='file'][id*='resume']", 6),
            ("input[type='file'][name*='cv']", 6),
            ("input[type='file'][id*='cv']", 6),
            ("input[type='file'][name*='cover']", 6),
            ("input[type='file'][id*='cover']", 6),
            ("input[type='file'][name*='letter']", 6),
            ("input[type='file'][id*='letter']", 6),
        ]
        for selector, max_candidates in selector_candidates:
            locator = page.locator(selector)
            count = 0
            with suppress(Exception):
                count = await locator.count()
            for index in range(min(count, max_candidates)):
                candidate = locator.nth(index)
                metadata: dict[str, Any] = {}
                with suppress(Exception):
                    metadata = await candidate.evaluate(
                        """el => {
                            const normalize = (value) => String(value || "").toLowerCase();
                            const id = normalize(el.id);
                            const name = normalize(el.getAttribute("name"));
                            const aria = normalize(el.getAttribute("aria-label"));
                            const accept = normalize(el.getAttribute("accept"));
                            const required = Boolean(el.required) || normalize(el.getAttribute("aria-required")) === "true";
                            const labelNode = el.id ? document.querySelector(`label[for="${el.id.replace(/"/g, '\\"')}"]`) : null;
                            const label = normalize(labelNode ? labelNode.textContent : "");
                            const helper = normalize((el.closest("div, section, fieldset") || document.body).textContent).slice(0, 500);
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            const visible = style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
                            return { id, name, aria, accept, label, helper, visible, required };
                        }"""
                    )
                if not isinstance(metadata, dict):
                    metadata = {}
                label = str(metadata.get("label", "")).strip()
                name = str(metadata.get("name", "")).strip()
                field_id = str(metadata.get("id", "")).strip()
                helper = str(metadata.get("helper", "")).strip()
                slot = classify_file_slot(
                    label=label,
                    name=name,
                    field_id=field_id,
                    helper_text=helper,
                ) or "other"
                semantic_key = field_id or name or label or f"file_input_{index}"
                score = 0
                if slot == preferred_slot:
                    score += 14
                if bool(metadata.get("required", False)):
                    score += 4
                if bool(metadata.get("visible", False)):
                    score += 1
                if preferred_slot == "resume" and ("resume" in selector or "cv" in selector):
                    score += 3
                if preferred_slot == "cover_letter" and ("cover" in selector or "letter" in selector):
                    score += 3
                ranked.append(
                    {
                        "selector": f"{selector}:nth({index})",
                        "candidate": candidate,
                        "score": score,
                        "slot": slot,
                        "required": bool(metadata.get("required", False)),
                        "label": label or name or field_id or slot,
                        "semantic_key": semantic_key,
                    }
                )
        ranked.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
        return ranked

    async def _verify_file_input_upload(
        self,
        *,
        candidate: Any,
        expected_filename: str,
    ) -> bool:
        uploaded_payload = getattr(candidate, "uploaded_payload", None)
        if isinstance(uploaded_payload, dict):
            uploaded_name = normalize_text(str(uploaded_payload.get("name", "")))
            if uploaded_name == normalize_text(expected_filename):
                return True
        uploaded_path = getattr(candidate, "uploaded_path", None)
        if isinstance(uploaded_path, str) and uploaded_path:
            return normalize_text(os.path.basename(uploaded_path)) == normalize_text(expected_filename)
        with suppress(Exception):
            info = await candidate.evaluate(
                """el => ({
                    count: (el.files && el.files.length) || 0,
                    first_name: el.files && el.files[0] ? String(el.files[0].name || "") : ""
                })"""
            )
            if isinstance(info, dict):
                count = int(info.get("count", 0))
                first_name = normalize_text(str(info.get("first_name", "")))
                if count > 0 and (
                    not expected_filename
                    or first_name == normalize_text(expected_filename)
                ):
                    return True
        return False

    async def _upload_file_via_file_chooser(
        self,
        *,
        page: Any,
        file_payload: dict[str, Any],
        slot: str,
    ) -> bool:
        if not hasattr(page, "expect_file_chooser"):
            return False
        trigger_selectors: list[str]
        if slot == "cover_letter":
            trigger_selectors = [
                "button:has-text('Attach cover letter')",
                "button:has-text('Upload cover letter')",
                "label:has-text('Cover Letter')",
            ]
        else:
            trigger_selectors = [
                "button:has-text('Upload resume')",
                "button:has-text('Attach resume')",
                "button:has-text('Attach Resume/CV')",
                "button:has-text('Attach')",
                "label:has-text('Resume')",
                "label:has-text('CV')",
            ]
        for selector in trigger_selectors:
            trigger = page.locator(selector).first
            with suppress(Exception):
                async with page.expect_file_chooser(timeout=self.action_timeout_ms) as chooser_info:
                    await trigger.click(timeout=min(self.action_timeout_ms, 1200))
                chooser = await chooser_info.value
                await chooser.set_files(file_payload)
                return True
        return False

    async def _fill_greenhouse_question_fields(
        self,
        *,
        page: Any,
        request: ApplyRunRequest,
        values: dict[str, str | bool],
    ) -> int:
        if not hasattr(page, "locator"):
            return 0
        filled = 0
        locator = page.locator("input[id^='question_'], textarea[id^='question_'], select[id^='question_']")
        count = 0
        with suppress(Exception):
            count = await locator.count()
        for index in range(min(count, 40)):
            candidate = locator.nth(index)
            try:
                if not await candidate.is_visible(timeout=200):
                    continue
                metadata = await candidate.evaluate(
                    """el => ({
                        id: el.id || "",
                        name: el.getAttribute("name") || "",
                        label: (document.querySelector(`label[for="${el.id}"]`)?.innerText || el.getAttribute("aria-label") || "").trim(),
                        type: (el.getAttribute("type") || "").toLowerCase(),
                        tag: (el.tagName || "").toLowerCase(),
                        role: (el.getAttribute("role") || "").toLowerCase(),
                        helper: (el.getAttribute("aria-describedby") || "").trim(),
                        options: (el.tagName || "").toLowerCase() === "select"
                            ? Array.from(el.options || []).map(opt => String(opt.label || opt.textContent || "").trim()).filter(Boolean)
                            : []
                    })"""
                )
                if not isinstance(metadata, dict):
                    continue
                semantic_key = str(
                    metadata.get("id", "")
                    or metadata.get("name", "")
                    or metadata.get("label", "")
                    or "unknown_field"
                )
                label = str(metadata.get("label", ""))
                field_name = str(metadata.get("name", ""))
                field_id = str(metadata.get("id", ""))
                input_type = str(metadata.get("type", ""))
                tag_name = str(metadata.get("tag", ""))
                role = str(metadata.get("role", ""))
                helper_text = str(metadata.get("helper", ""))
                option_values = [
                    str(item).strip()
                    for item in (metadata.get("options") or [])
                    if str(item).strip()
                ]
                if should_ignore_internal_field_candidate(
                    label=label,
                    name=field_name,
                    field_id=field_id,
                    tag_name=tag_name,
                    input_type=input_type,
                    role=role,
                ):
                    self._record_field_attempt(
                        semantic_key=semantic_key,
                        label=label,
                        adapter="candidate_filter",
                        attempted_value="",
                        success=False,
                        verified=False,
                        reason="internal_control_ignored",
                        intent=None,
                        widget_type=classify_widget_type(
                            tag_name=tag_name,
                            input_type=input_type,
                            role=role,
                        ),
                        constraint_mode=(
                            "option_only"
                            if is_option_constrained(
                                tag_name=tag_name,
                                input_type=input_type,
                                role=role,
                            )
                            else "free_text"
                        ),
                        failure_category="internal_control_ignored",
                    )
                    continue
                if self._is_field_circuit_open(semantic_key):
                    self._record_field_attempt(
                        semantic_key=semantic_key,
                        label=label,
                        adapter="circuit_breaker",
                        attempted_value="",
                        success=False,
                        verified=False,
                        reason="repeated_failure_skipped",
                        intent=None,
                        widget_type=classify_widget_type(
                            tag_name=tag_name,
                            input_type=input_type,
                            role=role,
                        ),
                        constraint_mode=(
                            "option_only"
                            if is_option_constrained(
                                tag_name=tag_name,
                                input_type=input_type,
                                role=role,
                            )
                            else "free_text"
                        ),
                        failure_category="repeated_failure_skipped",
                    )
                    continue
                answer = self._resolve_dynamic_answer(
                    request=request,
                    values=values,
                    label=label,
                    name=field_name,
                    field_id=field_id,
                    input_type=input_type,
                    tag_name=tag_name,
                    role=role,
                    helper_text=helper_text,
                    options=option_values,
                )
                if answer is None:
                    intent = classify_field_intent(
                        label=label,
                        name=field_name,
                        field_id=field_id,
                        helper_text=helper_text,
                        tag_name=tag_name,
                        input_type=input_type,
                        role=role,
                        options=option_values,
                    )
                    self._record_field_attempt(
                        semantic_key=semantic_key,
                        label=label,
                        adapter="resolver",
                        attempted_value="",
                        success=False,
                        verified=False,
                        reason="resolver_returned_none",
                        intent=intent,
                        widget_type=classify_widget_type(
                            tag_name=tag_name,
                            input_type=input_type,
                            role=role,
                        ),
                        constraint_mode=(
                            "option_only"
                            if is_option_constrained(
                                tag_name=tag_name,
                                input_type=input_type,
                                role=role,
                            )
                            else "free_text"
                        ),
                        failure_category=(
                            "missing_profile_short_fact"
                            if intent == "short_fact_text"
                            else "no_valid_option_match"
                        ),
                    )
                    continue
                if await self._fill_locator_candidate(
                    page=page,
                    candidate=candidate,
                    value=answer,
                    field_label=label,
                ):
                    filled += 1
            except Exception:
                continue
        return filled

    async def _fill_generic_required_fields(
        self,
        *,
        page: Any,
        request: ApplyRunRequest,
        values: dict[str, str | bool],
    ) -> int:
        if not hasattr(page, "locator"):
            return 0
        filled = 0
        locator = page.locator(
            "input[required], textarea[required], select[required], [role='combobox'][aria-required='true']"
        )
        count = 0
        with suppress(Exception):
            count = await locator.count()
        for index in range(min(count, 80)):
            candidate = locator.nth(index)
            try:
                if not await candidate.is_visible(timeout=200):
                    continue
                metadata = await candidate.evaluate(
                    """el => ({
                        id: el.id || "",
                        name: el.getAttribute("name") || "",
                        label: (document.querySelector(`label[for="${el.id}"]`)?.innerText || el.getAttribute("aria-label") || "").trim(),
                        type: (el.getAttribute("type") || "").toLowerCase(),
                        tag: (el.tagName || "").toLowerCase(),
                        role: (el.getAttribute("role") || "").toLowerCase(),
                        helper: (el.getAttribute("aria-describedby") || "").trim(),
                        options: (el.tagName || "").toLowerCase() === "select"
                            ? Array.from(el.options || []).map(opt => String(opt.label || opt.textContent || "").trim()).filter(Boolean)
                            : []
                    })"""
                )
                if not isinstance(metadata, dict):
                    continue
                semantic_key = str(
                    metadata.get("id", "")
                    or metadata.get("name", "")
                    or metadata.get("label", "")
                    or "unknown_field"
                )
                label = str(metadata.get("label", ""))
                field_name = str(metadata.get("name", ""))
                field_id = str(metadata.get("id", ""))
                input_type = str(metadata.get("type", ""))
                tag_name = str(metadata.get("tag", ""))
                role = str(metadata.get("role", ""))
                helper_text = str(metadata.get("helper", ""))
                option_values = [
                    str(item).strip()
                    for item in (metadata.get("options") or [])
                    if str(item).strip()
                ]
                if should_ignore_internal_field_candidate(
                    label=label,
                    name=field_name,
                    field_id=field_id,
                    tag_name=tag_name,
                    input_type=input_type,
                    role=role,
                ):
                    self._record_field_attempt(
                        semantic_key=semantic_key,
                        label=label,
                        adapter="candidate_filter",
                        attempted_value="",
                        success=False,
                        verified=False,
                        reason="internal_control_ignored",
                        intent=None,
                        widget_type=classify_widget_type(
                            tag_name=tag_name,
                            input_type=input_type,
                            role=role,
                        ),
                        constraint_mode=(
                            "option_only"
                            if is_option_constrained(
                                tag_name=tag_name,
                                input_type=input_type,
                                role=role,
                            )
                            else "free_text"
                        ),
                        failure_category="internal_control_ignored",
                    )
                    continue
                if self._is_field_circuit_open(semantic_key):
                    self._record_field_attempt(
                        semantic_key=semantic_key,
                        label=label,
                        adapter="circuit_breaker",
                        attempted_value="",
                        success=False,
                        verified=False,
                        reason="repeated_failure_skipped",
                        intent=None,
                        widget_type=classify_widget_type(
                            tag_name=tag_name,
                            input_type=input_type,
                            role=role,
                        ),
                        constraint_mode=(
                            "option_only"
                            if is_option_constrained(
                                tag_name=tag_name,
                                input_type=input_type,
                                role=role,
                            )
                            else "free_text"
                        ),
                        failure_category="repeated_failure_skipped",
                    )
                    continue
                answer = self._resolve_dynamic_answer(
                    request=request,
                    values=values,
                    label=label,
                    name=field_name,
                    field_id=field_id,
                    input_type=input_type,
                    tag_name=tag_name,
                    role=role,
                    helper_text=helper_text,
                    options=option_values,
                )
                if answer is None:
                    intent = classify_field_intent(
                        label=label,
                        name=field_name,
                        field_id=field_id,
                        helper_text=helper_text,
                        tag_name=tag_name,
                        input_type=input_type,
                        role=role,
                        options=option_values,
                    )
                    self._record_field_attempt(
                        semantic_key=semantic_key,
                        label=label,
                        adapter="resolver",
                        attempted_value="",
                        success=False,
                        verified=False,
                        reason="resolver_returned_none",
                        intent=intent,
                        widget_type=classify_widget_type(
                            tag_name=tag_name,
                            input_type=input_type,
                            role=role,
                        ),
                        constraint_mode=(
                            "option_only"
                            if is_option_constrained(
                                tag_name=tag_name,
                                input_type=input_type,
                                role=role,
                            )
                            else "free_text"
                        ),
                        failure_category=(
                            "missing_profile_short_fact"
                            if intent == "short_fact_text"
                            else "no_valid_option_match"
                        ),
                    )
                    continue
                if await self._fill_locator_candidate(
                    page=page,
                    candidate=candidate,
                    value=answer,
                    field_label=label,
                ):
                    filled += 1
            except Exception:
                continue
        return filled

    def _resolve_dynamic_answer(
        self,
        *,
        request: ApplyRunRequest,
        values: dict[str, str | bool],
        label: str,
        name: str,
        field_id: str,
        input_type: str,
        tag_name: str,
        role: str,
        helper_text: str,
        options: list[str] | None = None,
    ) -> str | None:
        normalized_options = [str(item).strip() for item in (options or []) if str(item).strip()]
        haystack = normalize_text(f"{label} {name} {field_id} {helper_text}")
        if not haystack:
            return None

        intent = classify_field_intent(
            label=label,
            name=name,
            field_id=field_id,
            helper_text=helper_text,
            tag_name=tag_name,
            input_type=input_type,
            role=role,
            options=normalized_options,
        )
        widget_type = classify_widget_type(
            tag_name=tag_name,
            input_type=input_type,
            role=role,
        )
        option_constrained = is_option_constrained(
            tag_name=tag_name,
            input_type=input_type,
            role=role,
        ) or intent in {"binary", "option_constrained"}
        combobox_without_options = (
            widget_type in {"custom_combobox", "listbox"} and not normalized_options
        )
        strict_option_only = option_constrained and not combobox_without_options

        def pick_option(preferred: str | None) -> str | None:
            if not normalized_options:
                return None
            selected = self._choose_option_value(
                label=label,
                name=name,
                field_id=field_id,
                options=normalized_options,
                preferred_value=preferred,
                allow_llm_fallback=True,
            )
            if selected:
                return selected
            if preferred:
                return best_option_match(preferred, normalized_options)
            return None

        def resolve_value(preferred: str | None) -> str | None:
            selected = pick_option(preferred)
            if selected:
                return selected
            if preferred:
                cleaned = str(preferred).strip()
                if cleaned and not strict_option_only:
                    return cleaned
            return None

        if normalize_text(input_type) == "checkbox":
            if any(token in haystack for token in ("agree", "consent", "privacy", "terms")):
                return "yes"
            if "authorized" in haystack or "authorization" in haystack:
                return "yes" if not bool(values.get("requires_sponsorship")) else "no"

        target_work_city = str(values.get("target_work_city", "")).strip()
        target_work_state = str(values.get("target_work_state", "")).strip()
        target_work_country = str(values.get("target_work_country", "")).strip()
        profile_city = target_work_city or str(values.get("city", "")).strip()
        profile_state = target_work_state or str(values.get("state", "")).strip()
        profile_country = target_work_country or str(values.get("country", "")).strip()

        if "preferred" in haystack and "name" in haystack:
            return str(values.get("first_name", "")).strip() or str(values.get("full_name", "")).strip()
        if "first name" in haystack or ("first" in haystack and "name" in haystack):
            return str(values.get("first_name", "")).strip()
        if "last name" in haystack or ("last" in haystack and "name" in haystack):
            return str(values.get("last_name", "")).strip()
        if "email" in haystack:
            return str(values.get("email", "")).strip()
        if "phone" in haystack:
            return str(values.get("phone", "")).strip()
        if "company" in haystack and (
            "current" in haystack or "most recent" in haystack or "recent" in haystack
        ):
            company_value = str(values.get("current_company", "")).strip() or str(
                values.get("most_recent_company", "")
            ).strip()
            if (
                not company_value
                and self.short_fact_llm_fallback_enabled
            ):
                if "inferred_current_company" not in self._runtime_short_fact_cache:
                    self._runtime_short_fact_cache["inferred_current_company"] = (
                        self.synthesizer.infer_company_from_resume(
                            request=request,
                            min_confidence=self.form_llm_min_confidence,
                        )
                    )
                company_value = str(
                    self._runtime_short_fact_cache.get("inferred_current_company", "") or ""
                ).strip()
            return resolve_value(company_value)
        if "title" in haystack and (
            "current" in haystack or "most recent" in haystack or "recent" in haystack
        ):
            title_value = str(values.get("current_title", "")).strip()
            return resolve_value(title_value)

        if "from where do you intend to work" in haystack:
            preferred = (
                f"{profile_city}, {profile_state}"
                if profile_city and profile_state
                else profile_city or profile_country
            )
            return resolve_value(preferred)
        if (
            ("location" in haystack or "city" in haystack)
            and "authorization" not in haystack
            and "authorized" not in haystack
        ):
            return resolve_value(profile_city)
        if (
            "state" in haystack
            and "authorization" not in haystack
            and "authorized" not in haystack
        ):
            return resolve_value(profile_state)
        if (
            "country" in haystack
            and "authorization" not in haystack
            and "authorized" not in haystack
        ):
            return resolve_value(profile_country)
        if (
            "located in colorado" in haystack
            or "european economic area" in haystack
            or "switzerland" in haystack
            or "united kingdom" in haystack
        ):
            preferred = profile_country or "United States"
            return resolve_value(preferred)
        if "linkedin" in haystack:
            return str(values.get("linkedin", "")).strip()
        if "website" in haystack or "portfolio" in haystack:
            return str(values.get("portfolio", "")).strip()
        if "github" in haystack:
            return str(values.get("github", "")).strip()
        if "cover letter" in haystack or "cover_letter" in haystack:
            if self.dev_review_mode:
                return None
            if strict_option_only:
                return resolve_value("I agree")
            return str(values.get("cover_letter", "")).strip()
        if "sponsor" in haystack:
            preferred = "Yes" if bool(values.get("requires_sponsorship")) else "No"
            return resolve_value(preferred)
        if "work authorization" in haystack or "authorized" in haystack:
            sponsorship_based = "No" if bool(values.get("requires_sponsorship")) else "Yes"
            preferred = str(values.get("work_authorization", "")).strip() or sponsorship_based
            selected = resolve_value(preferred)
            if selected:
                return selected
            return resolve_value(sponsorship_based)
        if "pronoun" in haystack:
            custom = self.synthesizer.resolve_typed_answer(
                request=request,
                question_key="pronouns",
            )
            preferred = custom or "Prefer not to say"
            return resolve_value(preferred)
        if "relocate" in haystack:
            preferred = "Yes" if bool(values.get("willing_to_relocate")) else "No"
            return resolve_value(preferred)
        if "privacy notice" in haystack or "consent" in haystack or "agree" in haystack:
            preferred = "I agree"
            if normalize_text(input_type) == "checkbox":
                preferred = "yes"
            return resolve_value(preferred)
        if "how did you hear" in haystack:
            custom = self.synthesizer.resolve_typed_answer(
                request=request,
                question_key="how_did_you_hear_about_this_job",
            )
            preferred = custom or "LinkedIn"
            return resolve_value(preferred)
        if "gender identity" in haystack or "race" in haystack or "ethnicity" in haystack:
            sensitive_key = "gender"
            if "race" in haystack or "ethnicity" in haystack:
                sensitive_key = "race_ethnicity"
            preferred = self.synthesizer.resolve_sensitive_answer(
                request=request,
                key=sensitive_key,
            )
            return resolve_value(preferred)
        if "veteran" in haystack:
            preferred = self.synthesizer.resolve_sensitive_answer(
                request=request,
                key="veteran_status",
            )
            return resolve_value(preferred)
        if "disability" in haystack:
            preferred = self.synthesizer.resolve_sensitive_answer(
                request=request,
                key="disability_status",
            )
            return resolve_value(preferred)
        if "sexual orientation" in haystack or "transgender" in haystack or "lgbt" in haystack:
            preferred = self.synthesizer.resolve_sensitive_answer(
                request=request,
                key="gender",
            )
            return resolve_value(preferred)
        if "currently employed" in haystack:
            return resolve_value("No")

        inferred_key = re.sub(r"[^a-z0-9]+", "_", (label or name or field_id).lower()).strip("_")
        if inferred_key:
            custom_answer = self.synthesizer.resolve_typed_answer(
                request=request,
                question_key=inferred_key,
            )
            if custom_answer:
                return resolve_value(custom_answer)

        selected = resolve_value(None)
        if selected:
            return selected
        if strict_option_only:
            return None
        if intent == "long_form_text":
            return self.synthesizer.answer_question(
                request=request,
                label=label,
                name=name or field_id or None,
                options=normalized_options or None,
            )
        return None

    async def _audit_required_fields(self, page: Any) -> list[str]:
        fallback_count = int(self._required_audit_stats.get("required_audit_fallback_used", 0))
        if not hasattr(page, "evaluate"):
            return list(self._last_required_unresolved)
        self._required_audit_stats = {
            "ghost_required_ignored": 0,
            "required_audit_fallback_used": fallback_count,
        }
        try:
            payload = await page.evaluate(
                """() => {
                    const labels = [];
                    let ghostIgnored = 0;
                    const seen = new Set();
                    const candidates = Array.from(
                        document.querySelectorAll("input, textarea, select, [role='combobox']")
                    );
                    const isVisible = (el) => {
                        const style = window.getComputedStyle(el);
                        if (style.visibility === "hidden" || style.display === "none") return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                    const isPlaceholder = (value) => {
                        const normalized = normalize(value).toLowerCase();
                        return (
                            normalized === "" ||
                            normalized === "select..." ||
                            normalized === "select" ||
                            normalized === "choose" ||
                            normalized === "choose..." ||
                            normalized === "please select"
                        );
                    };
                    const hasRequiredStar = (value) => /\\*/.test(normalize(value));
                    const inferLabel = (el) => {
                        const fromFor = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
                        const wrapped = el.closest("label");
                        const aria = el.getAttribute("aria-label") || "";
                        const name = el.getAttribute("name") || "";
                        const id = el.id || "";
                        const label = normalize(
                            (fromFor && fromFor.innerText) ||
                            (wrapped && wrapped.innerText) ||
                            aria
                        );
                        return { label, name: normalize(name), id: normalize(id), aria: normalize(aria) };
                    };
                    const comboboxFilled = (el) => {
                        const ownValue = normalize(el.value || el.textContent || "");
                        if (ownValue && !isPlaceholder(ownValue)) return true;
                        const container = el.closest("[class*='field'], .application-field, .field, [data-testid*='question'], [id*='question']");
                        const scope = container || el.parentElement || document.body;
                        const hiddenInputs = scope.querySelectorAll("input[type='hidden']");
                        for (const input of Array.from(hiddenInputs)) {
                            const hiddenValue = normalize(input.value || "");
                            if (hiddenValue && !isPlaceholder(hiddenValue)) return true;
                        }
                        const selectedNodes = scope.querySelectorAll(
                            ".select__single-value, [class*='singleValue'], .select__multi-value__label, [class*='multiValue'] [class*='label'], [class*='value-container'] [class*='singleValue'], .selected-value, .token, .chip"
                        );
                        for (const node of Array.from(selectedNodes)) {
                            if (!isVisible(node)) continue;
                            const text = normalize(node.textContent);
                            if (text && !isPlaceholder(text)) return true;
                        }
                        return false;
                    };

                    for (const el of candidates) {
                        if (!isVisible(el)) continue;
                        if (el.disabled) continue;
                        const type = String(el.getAttribute("type") || "").toLowerCase();
                        if (type === "hidden") continue;
                        const role = String(el.getAttribute("role") || "").toLowerCase();
                        const resolved = inferLabel(el);
                        const requiredByLabel = hasRequiredStar(resolved.label);
                        const required =
                            el.required === true ||
                            String(el.getAttribute("aria-required") || "").toLowerCase() === "true" ||
                            ((role === "combobox" || role === "listbox") && requiredByLabel);
                        if (!required) continue;
                        let isFilled = true;
                        if (type === "checkbox" || type === "radio") {
                            isFilled = Boolean(el.checked);
                        } else if (type === "file") {
                            isFilled = Boolean(el.files && el.files.length > 0);
                        } else if (el.tagName.toLowerCase() === "select") {
                            const value = normalize(el.value || "");
                            isFilled = Boolean(value && !isPlaceholder(value));
                        } else if (role === "combobox" || role === "listbox") {
                            isFilled = comboboxFilled(el);
                        } else {
                            const value = normalize(el.value || "");
                            isFilled = Boolean(value && !isPlaceholder(value));
                        }
                        if (isFilled) continue;
                        const hasIdentity = Boolean(resolved.label || resolved.name || resolved.id || resolved.aria);
                        if (!hasIdentity) {
                            ghostIgnored += 1;
                            continue;
                        }
                        const labelText = resolved.label || resolved.aria || resolved.name || resolved.id;
                        const normalized = normalize(labelText).slice(0, 120);
                        if (!normalized || seen.has(normalized)) continue;
                        seen.add(normalized);
                        labels.push(normalized);
                    }

                    const fileInputs = Array.from(document.querySelectorAll("input[type='file']"));
                    for (const fileEl of fileInputs) {
                        if (fileEl.disabled) continue;
                        const required =
                            fileEl.required === true ||
                            String(fileEl.getAttribute("aria-required") || "").toLowerCase() === "true";
                        if (!required) continue;
                        const hasFiles = Boolean(fileEl.files && fileEl.files.length > 0);
                        if (hasFiles) continue;
                        const resolved = inferLabel(fileEl);
                        const hasIdentity = Boolean(resolved.label || resolved.name || resolved.id || resolved.aria);
                        if (!hasIdentity) {
                            ghostIgnored += 1;
                            continue;
                        }
                        const labelText = resolved.label || resolved.aria || resolved.name || resolved.id;
                        const normalized = normalize(labelText).slice(0, 120);
                        if (!normalized || seen.has(normalized)) continue;
                        seen.add(normalized);
                        labels.push(normalized);
                    }

                    return { labels, ghostIgnored };
                }"""
            )
            if isinstance(payload, dict):
                labels = payload.get("labels")
                ghost_ignored = int(payload.get("ghostIgnored", 0))
                self._required_audit_stats = {
                    "ghost_required_ignored": max(ghost_ignored, 0),
                    "required_audit_fallback_used": fallback_count,
                }
                if isinstance(labels, list):
                    resolved = [str(item) for item in labels if str(item).strip()]
                    if not resolved:
                        failed_required = any(
                            not attempt.success
                            and normalize_text(attempt.failure_category or "")
                            in {
                                "no_valid_option_match",
                                "combobox_option_not_clickable",
                                "combobox_options_not_discovered",
                                "missing_profile_short_fact",
                                "unable_to_toggle",
                            }
                            for attempt in self._field_attempt_traces[-80:]
                        )
                        if failed_required and self._last_required_unresolved:
                            self._required_audit_stats["required_audit_fallback_used"] = (
                                int(self._required_audit_stats.get("required_audit_fallback_used", 0)) + 1
                            )
                            return list(self._last_required_unresolved)
                    self._last_required_unresolved = list(resolved)
                    return resolved
            if isinstance(payload, list):
                resolved = [str(item) for item in payload if str(item).strip()]
                self._last_required_unresolved = list(resolved)
                return resolved
        except Exception:
            self._required_audit_stats["required_audit_fallback_used"] = (
                int(self._required_audit_stats.get("required_audit_fallback_used", 0)) + 1
            )
            return list(self._last_required_unresolved)
        return []

    async def _submit_and_confirm(
        self,
        *,
        page: Any,
        attempt: ApplyAttemptRecord,
    ) -> ApplyAttemptRecord:
        unresolved = await self._audit_required_fields(page)
        if unresolved:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.blocked,
                    "failure_code": FailureCode.form_validation_failed,
                    "failure_reason": (
                        "Required fields unresolved before submit: "
                        + ", ".join(unresolved[:6])
                    ),
                    "submitted_at": None,
                }
            )

        clicked = await self._click_submit_button(page)
        if not clicked:
            return attempt.model_copy(
                update={
                    "status": ApplyAttemptStatus.blocked,
                    "failure_code": FailureCode.form_validation_failed,
                    "failure_reason": "Unable to locate a submit/apply button",
                    "submitted_at": None,
                }
            )

        submission_signals = {"network_submit": False}

        def _handle_request(request_obj: Any) -> None:
            method = str(getattr(request_obj, "method", "GET")).upper()
            if method == "GET":
                return
            url = str(getattr(request_obj, "url", "")).lower()
            payload = ""
            with suppress(Exception):
                payload = str(getattr(request_obj, "post_data", "") or "").lower()
            haystack = f"{url} {payload}"
            if any(token in haystack for token in {"submit", "application", "apply", "candidate"}):
                submission_signals["network_submit"] = True

        page.on("request", _handle_request)
        deadline = time.monotonic() + float(self.submit_timeout_seconds)
        try:
            while time.monotonic() < deadline:
                if (
                    submission_signals["network_submit"]
                    or self._is_submission_url(page.url)
                    or await self._has_confirmation_text(page)
                ):
                    return attempt.model_copy(
                        update={
                            "status": ApplyAttemptStatus.submitted,
                            "submitted_at": utc_now(),
                            "failure_code": None,
                            "failure_reason": None,
                        }
                    )
                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            with suppress(Exception):
                page.remove_listener("request", _handle_request)

        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.failed,
                "failure_code": FailureCode.timeout,
                "failure_reason": (
                    f"Submit confirmation not detected within {self.submit_timeout_seconds} seconds"
                ),
                "submitted_at": None,
            }
        )

    async def _click_submit_button(self, page: Any) -> bool:
        if not hasattr(page, "locator"):
            return False
        selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit application')",
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
            "button:has-text('Continue')",
        ]
        for selector in selectors:
            locator = page.locator(selector)
            count = 0
            with suppress(Exception):
                count = await locator.count()
            for index in range(min(count, 4)):
                candidate = locator.nth(index)
                with suppress(Exception):
                    if not await candidate.is_visible(timeout=200):
                        continue
                    is_disabled = False
                    with suppress(Exception):
                        is_disabled = await candidate.is_disabled()
                    if is_disabled:
                        continue
                    await candidate.click(timeout=self.action_timeout_ms)
                    return True
        return False

    async def _await_manual_submit(
        self,
        *,
        page: Any,
        attempt: ApplyAttemptRecord,
    ) -> ApplyAttemptRecord:
        submission_signals = {"network_submit": False}

        def _handle_request(request_obj: Any) -> None:
            method = str(getattr(request_obj, "method", "GET")).upper()
            if method == "GET":
                return
            url = str(getattr(request_obj, "url", "")).lower()
            payload = ""
            with suppress(Exception):
                payload = str(getattr(request_obj, "post_data", "") or "").lower()
            haystack = f"{url} {payload}"
            if any(token in haystack for token in {"submit", "application", "apply", "candidate"}):
                submission_signals["network_submit"] = True

        page.on("request", _handle_request)
        deadline = time.monotonic() + float(self.submit_timeout_seconds)
        try:
            while time.monotonic() < deadline:
                if (
                    submission_signals["network_submit"]
                    or self._is_submission_url(page.url)
                    or await self._has_confirmation_text(page)
                ):
                    return attempt.model_copy(
                        update={
                            "status": ApplyAttemptStatus.submitted,
                            "submitted_at": utc_now(),
                            "failure_code": None,
                            "failure_reason": None,
                        }
                    )
                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            with suppress(Exception):
                page.remove_listener("request", _handle_request)

        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.blocked,
                "failure_code": FailureCode.manual_review_timeout,
                "failure_reason": (
                    f"Manual submit not detected within {self.submit_timeout_seconds} seconds"
                ),
                "submitted_at": None,
            }
        )

    def _is_submission_url(self, url: str | None) -> bool:
        lower_url = (url or "").lower()
        return any(token in lower_url for token in self._SUBMIT_URL_TOKENS)

    async def _has_confirmation_text(self, page: Any) -> bool:
        text = ""
        with suppress(Exception):
            text = str(
                await page.locator("body").inner_text(
                    timeout=min(self.action_timeout_ms, 1500)
                )
            )
        if not text:
            return False
        return bool(self._SUBMIT_TEXT_RE.search(text))

    @staticmethod
    def _standard_terminal_attempt(attempt: ApplyAttemptRecord) -> ApplyAttemptRecord:
        return attempt.model_copy(
            update={
                "status": ApplyAttemptStatus.succeeded,
                "submitted_at": utc_now(),
                "failure_code": None,
                "failure_reason": None,
            }
        )

    @staticmethod
    def _is_sensitive_trace_field(
        *,
        label: str,
        semantic_key: str,
        input_type: str,
    ) -> bool:
        normalized = normalize_text(f"{label} {semantic_key}")
        if input_type in {"password", "file"}:
            return True
        sensitive_tokens = (
            "gender",
            "race",
            "ethnicity",
            "veteran",
            "disability",
            "ssn",
            "social security",
            "date of birth",
            "dob",
            "passport",
        )
        return any(token in normalized for token in sensitive_tokens)

    def _redact_trace_value(
        self,
        *,
        value: str,
        label: str,
        semantic_key: str,
        input_type: str = "",
    ) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if self._is_sensitive_trace_field(
            label=label,
            semantic_key=semantic_key,
            input_type=normalize_text(input_type),
        ):
            return "[REDACTED]"
        return text[:320]

    def _snapshot_field_trace_rows(self, *, snapshot: Any) -> list[dict[str, Any]]:
        fields = list(getattr(snapshot, "fields", ()) or [])
        rows: list[dict[str, Any]] = []
        for field in fields[:200]:
            semantic_key = str(getattr(field, "semantic_key", "") or "unknown_field")
            label = str(getattr(field, "label", "") or "")
            input_type = str(getattr(field, "input_type", "") or "")
            value = self._redact_trace_value(
                value=str(getattr(field, "value", "") or ""),
                label=label,
                semantic_key=semantic_key,
                input_type=input_type,
            )
            options_payload: list[dict[str, Any]] = []
            raw_options = list(getattr(field, "options", ()) or [])
            for option in raw_options[:40]:
                options_payload.append(
                    {
                        "label": str(getattr(option, "label", "") or ""),
                        "value": str(getattr(option, "value", "") or ""),
                        "selected": bool(getattr(option, "selected", False)),
                    }
                )
            rows.append(
                {
                    "semantic_key": semantic_key,
                    "dom_path": str(getattr(field, "dom_path", "") or ""),
                    "tag_name": str(getattr(field, "tag_name", "") or ""),
                    "input_type": input_type,
                    "role": str(getattr(field, "role", "") or ""),
                    "required": bool(getattr(field, "required", False)),
                    "visible": bool(getattr(field, "visible", False)),
                    "enabled": bool(getattr(field, "enabled", False)),
                    "label": label,
                    "helper_text": str(getattr(field, "helper_text", "") or ""),
                    "error_text": str(getattr(field, "error_text", "") or ""),
                    "placeholder": str(getattr(field, "placeholder", "") or ""),
                    "name": str(getattr(field, "name", "") or ""),
                    "field_id": str(getattr(field, "field_id", "") or ""),
                    "value": value,
                    "section_heading": str(getattr(field, "section_heading", "") or ""),
                    "intent": str(getattr(field, "intent", "") or ""),
                    "widget_type": str(getattr(field, "widget_type", "") or ""),
                    "constraint_mode": str(getattr(field, "constraint_mode", "") or ""),
                    "file_slot": getattr(field, "file_slot", None),
                    "options": options_payload,
                }
            )
        return rows

    def _persist_form_trace(
        self,
        *,
        attempt_id: str,
        diagnostics: dict[str, Any],
    ) -> str | None:
        if not diagnostics:
            return None
        payload = {
            "attempt_id": attempt_id,
            "captured_at": utc_now().isoformat(),
            "diagnostics": diagnostics,
            "llm_calls_used": self._llm_calls_used,
            "llm_call_budget": self.form_llm_max_calls,
            "combobox_trace": self._combobox_trace[-120:],
            "field_attempts": [
                {
                    "semantic_key": item.semantic_key,
                    "label": item.label,
                    "adapter": item.adapter,
                    "attempted_value": self._redact_trace_value(
                        value=item.attempted_value,
                        label=item.label,
                        semantic_key=item.semantic_key,
                    ),
                    "success": item.success,
                    "verified": item.verified,
                    "reason": item.reason,
                    "intent": item.intent,
                    "widget_type": item.widget_type,
                    "constraint_mode": item.constraint_mode,
                    "failure_category": item.failure_category,
                    "file_slot": item.file_slot,
                }
                for item in self._field_attempt_traces
            ],
        }
        path = f"/tmp/{attempt_id}-playwright-field-trace.json"
        with suppress(Exception):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"), indent=2)
            return path
        return None

    def _build_artifacts(self, attempt_id: str) -> list[ArtifactRef]:
        expires = utc_now() + timedelta(days=7)
        artifacts = [
            ArtifactRef(
                kind="html",
                url=f"s3://job-artifacts/{attempt_id}/playwright-final.html",
                expires_at=expires,
            )
        ]
        if self.capture_screenshots:
            artifacts.append(
                ArtifactRef(
                    kind="screenshot",
                    url=f"s3://job-artifacts/{attempt_id}/playwright-final.png",
                    expires_at=expires,
                )
            )
        if self._latest_trace_file_path:
            artifacts.append(
                ArtifactRef(
                    kind="field_trace",
                    url=f"s3://job-artifacts/{attempt_id}/playwright-field-trace.json",
                    expires_at=expires,
                )
            )
        return artifacts



__all__ = ["PlaywrightApplyExecutor"]
