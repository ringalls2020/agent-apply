from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import os
import re
import tempfile
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

    async def complete_attempt(
        self,
        *,
        attempt: ApplyAttemptRecord,
        request: ApplyRunRequest,
    ) -> ApplyAttemptRecord:
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
                await self._fill_application_form(page=page, request=request)

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
        }
        return values

    @staticmethod
    def _resume_file_payload(request: ApplyRunRequest) -> dict[str, Any] | None:
        profile_payload = request.profile_payload or {}
        resume_file = profile_payload.get("resume_file")
        return resume_file if isinstance(resume_file, dict) else None

    async def _fill_application_form(self, *, page: Any, request: ApplyRunRequest) -> None:
        values = self._build_fill_values(request)
        filled_count = 0

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
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name='state']", "input[name*='state']", "input[autocomplete='address-level1']"],
            value=values["state"],
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name*='country']", "input[id*='country']"],
            value=values["country"],
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name*='linkedin']", "input[id*='linkedin']"],
            value=values["linkedin"],
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name*='github']", "input[id*='github']"],
            value=values["github"],
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name*='portfolio']", "input[id*='portfolio']", "input[name*='website']"],
            value=values["portfolio"],
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name*='work_authorization']", "select[name*='work_authorization']"],
            value=values["work_authorization"],
            )
        )
        filled_count += int(
            await self._fill_boolean_field(
            page,
            selectors=["input[name*='sponsor']", "select[name*='sponsor']", "input[name*='requires_sponsorship']"],
            value=bool(values["requires_sponsorship"]),
            )
        )
        filled_count += int(
            await self._fill_boolean_field(
            page,
            selectors=["input[name*='relocate']", "select[name*='relocate']"],
            value=bool(values["willing_to_relocate"]),
            )
        )
        filled_count += int(
            await self._fill_text_field(
            page,
            selectors=["input[name*='experience']", "input[id*='experience']"],
            value=values["years_experience"],
            )
        )

        if await self._upload_resume_file(page=page, request=request):
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
                )
            )

        unresolved_required = await self._audit_required_fields(page)
        logger.info(
            "playwright_form_fill_summary",
            extra={
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

    async def _fill_boolean_field(self, page: Any, *, selectors: list[str], value: bool) -> bool:
        normalized = "yes" if value else "no"
        return await self._fill_text_field(page, selectors=selectors, value=normalized)

    async def _fill_text_field(self, page: Any, *, selectors: list[str], value: Any) -> bool:
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
                    ):
                        return True
                except Exception:
                    continue
        return False

    async def _fill_locator_candidate(self, *, page: Any, candidate: Any, value: str) -> bool:
        text = str(value).strip()
        if not text:
            return False
        try:
            tag_name = str(await candidate.evaluate("el => el.tagName.toLowerCase()"))
            input_type = str(await candidate.evaluate("el => (el.getAttribute('type') || '').toLowerCase()"))
            role = str(await candidate.evaluate("el => (el.getAttribute('role') || '').toLowerCase()"))
        except Exception:
            return False

        if input_type == "file":
            return False

        if input_type in {"checkbox", "radio"}:
            normalized = text.lower()
            should_check = normalized in {"yes", "true", "1", "agree", "i agree"}
            return await self._set_checkbox_or_radio(candidate=candidate, value=should_check)

        if tag_name == "select":
            with suppress(Exception):
                await candidate.select_option(label=text)
                return True
            with suppress(Exception):
                await candidate.select_option(value=text)
                return True
            return False

        if role == "combobox":
            return await self._fill_combobox(page=page, candidate=candidate, value=text)

        with suppress(Exception):
            await candidate.fill(text, timeout=self.action_timeout_ms)
            return True
        return False

    async def _fill_combobox(self, *, page: Any, candidate: Any, value: str) -> bool:
        with suppress(Exception):
            await candidate.click(timeout=self.action_timeout_ms)
        with suppress(Exception):
            await candidate.fill("")
        with suppress(Exception):
            await candidate.fill(value, timeout=self.action_timeout_ms)
        with suppress(Exception):
            option = page.get_by_role("option", name=re.compile(re.escape(value), re.IGNORECASE)).first
            await option.click(timeout=min(self.action_timeout_ms, 1200))
            return True
        with suppress(Exception):
            await page.keyboard.press("Enter")
            return True
        return False

    async def _set_checkbox_or_radio(self, *, candidate: Any, value: bool) -> bool:
        try:
            currently_checked = await candidate.is_checked()
        except Exception:
            currently_checked = False
        if currently_checked == value:
            return True
        with suppress(Exception):
            if value:
                await candidate.check(timeout=self.action_timeout_ms)
            else:
                await candidate.uncheck(timeout=self.action_timeout_ms)
            return True
        with suppress(Exception):
            await candidate.click(timeout=self.action_timeout_ms)
            return True
        return False

    async def _upload_resume_file(self, *, page: Any, request: ApplyRunRequest) -> bool:
        if not hasattr(page, "locator"):
            return False
        resume_file = self._resume_file_payload(request)
        if resume_file is None:
            return False
        filename = str(resume_file.get("filename", "resume.txt")).strip() or "resume.txt"
        content_base64 = str(resume_file.get("content_base64", "")).strip()
        if not content_base64:
            return False
        try:
            content = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("playwright_resume_upload_failed", extra={"reason": "invalid_base64"})
            return False
        if not content:
            logger.warning("playwright_resume_upload_failed", extra={"reason": "empty_payload"})
            return False

        tmp_path = ""
        try:
            suffix = os.path.splitext(filename)[1] or ".txt"
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="resume-upload-",
                suffix=suffix,
                delete=False,
            ) as handle:
                handle.write(content)
                tmp_path = handle.name

            selectors = [
                "input[type='file']#resume",
                "input[type='file'][name*='resume']",
                "input[type='file'][id*='resume']",
                "input[type='file'][name*='cv']",
                "input[type='file'][id*='cv']",
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
                        await candidate.set_input_files(tmp_path, timeout=self.action_timeout_ms)
                        logger.info(
                            "playwright_resume_upload_attempted",
                            extra={"selector": selector, "resume_filename": filename},
                        )
                        return True
        except Exception:
            logger.exception("playwright_resume_upload_failed")
        finally:
            if tmp_path:
                with suppress(Exception):
                    os.remove(tmp_path)
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
                        label: (document.querySelector(`label[for="${el.id}"]`)?.innerText || "").trim(),
                        type: (el.getAttribute("type") || "").toLowerCase(),
                        role: (el.getAttribute("role") || "").toLowerCase()
                    })"""
                )
                answer = self._resolve_dynamic_answer(
                    request=request,
                    values=values,
                    label=str(metadata.get("label", "")),
                    name=str(metadata.get("name", "")),
                    field_id=str(metadata.get("id", "")),
                    input_type=str(metadata.get("type", "")),
                )
                if answer is None:
                    continue
                if await self._fill_locator_candidate(
                    page=page,
                    candidate=candidate,
                    value=answer,
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
                        role: (el.getAttribute("role") || "").toLowerCase()
                    })"""
                )
                answer = self._resolve_dynamic_answer(
                    request=request,
                    values=values,
                    label=str(metadata.get("label", "")),
                    name=str(metadata.get("name", "")),
                    field_id=str(metadata.get("id", "")),
                    input_type=str(metadata.get("type", "")),
                )
                if answer is None:
                    continue
                if await self._fill_locator_candidate(
                    page=page,
                    candidate=candidate,
                    value=answer,
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
    ) -> str | None:
        haystack = f"{label} {name} {field_id}".strip().lower()
        if not haystack:
            return None

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
        if "location" in haystack or "city" in haystack:
            return str(values.get("city", "")).strip()
        if "state" in haystack:
            return str(values.get("state", "")).strip()
        if "country" in haystack:
            return str(values.get("country", "")).strip()
        if "linkedin" in haystack:
            return str(values.get("linkedin", "")).strip()
        if "website" in haystack or "portfolio" in haystack:
            return str(values.get("portfolio", "")).strip()
        if "github" in haystack:
            return str(values.get("github", "")).strip()
        if "cover letter" in haystack or "cover_letter" in haystack:
            if self.dev_review_mode:
                return None
            return str(values.get("cover_letter", "")).strip()
        if "sponsor" in haystack:
            return "Yes" if bool(values.get("requires_sponsorship")) else "No"
        if "work authorization" in haystack or "authorized" in haystack:
            if "present ability" in haystack or "permanently" in haystack:
                return "No" if bool(values.get("requires_sponsorship")) else "Yes"
            return str(values.get("work_authorization", "")).strip() or (
                "Yes" if not bool(values.get("requires_sponsorship")) else "No"
            )
        if "relocate" in haystack:
            return "Yes" if bool(values.get("willing_to_relocate")) else "No"
        if "privacy notice" in haystack or "consent" in haystack or "agree" in haystack:
            if input_type == "checkbox":
                return "yes"
            return "I agree"
        if "how did you hear" in haystack:
            custom = self.synthesizer.resolve_typed_answer(
                request=request,
                question_key="how_did_you_hear_about_this_job",
            )
            return custom or "LinkedIn"
        if "pronoun" in haystack:
            custom = self.synthesizer.resolve_typed_answer(
                request=request,
                question_key="pronouns",
            )
            return custom or "Prefer not to say"
        if "gender identity" in haystack or "race" in haystack or "ethnicity" in haystack:
            return "decline_to_answer"
        if "lgbt" in haystack or "veteran" in haystack or "disability" in haystack:
            return "decline_to_answer"
        if "currently employed" in haystack:
            return "No"

        inferred_key = re.sub(r"[^a-z0-9]+", "_", (label or name or field_id).lower()).strip("_")
        if inferred_key:
            custom_answer = self.synthesizer.resolve_typed_answer(
                request=request,
                question_key=inferred_key,
            )
            if custom_answer:
                return custom_answer

        if input_type in {"text", "textarea"}:
            return self.synthesizer.answer_question(
                request=request,
                label=label or None,
                name=name or field_id or None,
                options=None,
            )
        return None

    async def _audit_required_fields(self, page: Any) -> list[str]:
        if not hasattr(page, "evaluate"):
            return []
        try:
            unresolved = await page.evaluate(
                """() => {
                    const labels = [];
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
                    for (const el of candidates) {
                        if (!isVisible(el)) continue;
                        if (el.disabled) continue;
                        const required =
                            el.required === true ||
                            String(el.getAttribute("aria-required") || "").toLowerCase() === "true";
                        if (!required) continue;
                        const type = String(el.getAttribute("type") || "").toLowerCase();
                        if (type === "hidden") continue;
                        let isFilled = true;
                        if (type === "checkbox" || type === "radio") {
                            isFilled = Boolean(el.checked);
                        } else if (type === "file") {
                            isFilled = Boolean(el.files && el.files.length > 0);
                        } else if (el.tagName.toLowerCase() === "select") {
                            const value = String(el.value || "").trim();
                            isFilled = Boolean(value && value.toLowerCase() !== "select...");
                        } else {
                            const value = String(el.value || "").trim();
                            isFilled = Boolean(value && value.toLowerCase() !== "select...");
                        }
                        if (isFilled) continue;
                        const labelText =
                            (el.id ? document.querySelector(`label[for="${el.id}"]`)?.innerText : "") ||
                            el.getAttribute("aria-label") ||
                            el.getAttribute("name") ||
                            el.id ||
                            "unknown_field";
                        const normalized = String(labelText).trim().slice(0, 120);
                        if (!normalized || seen.has(normalized)) continue;
                        seen.add(normalized);
                        labels.push(normalized);
                    }
                    return labels;
                }"""
            )
            if isinstance(unresolved, list):
                return [str(item) for item in unresolved if str(item).strip()]
        except Exception:
            return []
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
        return artifacts



__all__ = ["PlaywrightApplyExecutor"]
