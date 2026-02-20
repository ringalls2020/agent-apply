from __future__ import annotations

from contextlib import suppress
import json
import logging
import os
import re
import time
from typing import Any

import httpx

from ..models import ApplyRunRequest
from .form_engine import best_option_match, normalize_text

logger = logging.getLogger(__name__)


class OpenAITextGenerator:
    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
        self.timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
        self.max_retries = max(int(os.getenv("OPENAI_MAX_RETRIES", "2")), 0)
        self.retry_base_seconds = max(float(os.getenv("OPENAI_RETRY_BASE_SECONDS", "0.75")), 0.1)
        self.retry_max_seconds = max(float(os.getenv("OPENAI_RETRY_MAX_SECONDS", "4")), 0.2)
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=self.timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _truncate_error_body(raw: str, *, limit: int = 1500) -> str:
        text = str(raw or "").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return f"{text[:limit]}...[truncated]"

    @classmethod
    def _extract_error_body(cls, response: httpx.Response | None) -> str:
        if response is None:
            return ""
        with suppress(Exception):
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    message = str(error.get("message", "")).strip()
                    error_type = str(error.get("type", "")).strip()
                    code = str(error.get("code", "")).strip()
                    param = str(error.get("param", "")).strip()
                    safe_error = {
                        "message": message,
                        "type": error_type,
                        "code": code,
                        "param": param,
                    }
                    return cls._truncate_error_body(json.dumps(safe_error, ensure_ascii=True))
            return cls._truncate_error_body(json.dumps(payload, ensure_ascii=True))
        with suppress(Exception):
            return cls._truncate_error_body(response.text)
        return ""

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code < 600

    def _retry_backoff_seconds(self, attempt_index: int) -> float:
        if attempt_index <= 0:
            return 0.0
        delay = self.retry_base_seconds * (2 ** (attempt_index - 1))
        return min(delay, self.retry_max_seconds)

    def generate(self, *, prompt: str) -> str | None:
        if not self.enabled:
            return None

        body: dict[str, Any] | None = None
        for attempt_index in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    "https://api.openai.com/v1/responses",
                    headers={
                        "authorization": f"Bearer {self.api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "input": prompt,
                        "max_output_tokens": 280,
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                parsed = response.json()
                body = parsed if isinstance(parsed, dict) else {}
                break
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                retryable = bool(
                    status_code is not None and self._is_retryable_status(int(status_code))
                )
                should_retry = retryable and attempt_index < self.max_retries
                logger.exception(
                    "openai_generation_failed",
                    extra={
                        "status_code": status_code,
                        "model": self.model,
                        "attempt_index": attempt_index + 1,
                        "max_attempts": self.max_retries + 1,
                        "will_retry": should_retry,
                        "response_error_body": self._extract_error_body(exc.response),
                    },
                )
                if should_retry:
                    time.sleep(self._retry_backoff_seconds(attempt_index + 1))
                    continue
                return None
            except (httpx.TimeoutException, httpx.TransportError):
                should_retry = attempt_index < self.max_retries
                logger.exception(
                    "openai_generation_failed",
                    extra={
                        "model": self.model,
                        "attempt_index": attempt_index + 1,
                        "max_attempts": self.max_retries + 1,
                        "will_retry": should_retry,
                        "failure_kind": "transport_or_timeout",
                    },
                )
                if should_retry:
                    time.sleep(self._retry_backoff_seconds(attempt_index + 1))
                    continue
                return None
            except Exception:
                logger.exception(
                    "openai_generation_failed",
                    extra={
                        "model": self.model,
                        "attempt_index": attempt_index + 1,
                        "max_attempts": self.max_retries + 1,
                        "will_retry": False,
                    },
                )
                return None

        if not body:
            return None

        text = body.get("output_text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        output = body.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") in {"output_text", "text"}:
                        candidate = str(block.get("text", "")).strip()
                        if candidate:
                            return candidate
        return None

    @staticmethod
    def _extract_first_json_object(raw: str) -> dict[str, Any] | None:
        text = str(raw or "").strip()
        if not text:
            return None
        with suppress(Exception):
            decoded = json.loads(text)
            if isinstance(decoded, dict):
                return decoded
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        with suppress(Exception):
            decoded = json.loads(match.group(0))
            if isinstance(decoded, dict):
                return decoded
        return None

    def generate_json(self, *, prompt: str) -> dict[str, Any] | None:
        raw = self.generate(prompt=prompt)
        if not raw:
            return None
        return self._extract_first_json_object(raw)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class FormAnswerSynthesizer:
    def __init__(self, *, text_generator: OpenAITextGenerator | None = None) -> None:
        self.text_generator = text_generator or OpenAITextGenerator()

    @staticmethod
    def _normalized_decline_option(options: list[str]) -> str | None:
        decline_tokens = (
            "prefer not",
            "decline",
            "i don't wish",
            "i do not wish",
            "don't wish",
            "do not wish",
            "not listed",
            "prefer not to say",
            "rather not say",
        )
        for option in options:
            text = str(option).strip()
            if not text:
                continue
            normalized = normalize_text(text)
            if any(token in normalized for token in decline_tokens):
                return text
        return None

    @staticmethod
    def _should_prefer_decline_option(
        *,
        label: str | None,
        name: str | None,
        field_id: str | None,
    ) -> bool:
        haystack = normalize_text(
            " ".join(part for part in [label or "", name or "", field_id or ""] if part)
        )
        if not haystack:
            return False
        sensitive_tokens = (
            "gender",
            "ethnicity",
            "race",
            "veteran",
            "disability",
            "sexual orientation",
            "transgender",
            "self-identification",
            "demographic",
            "pronoun",
        )
        return any(token in haystack for token in sensitive_tokens)

    @staticmethod
    def _clean_company_name(value: str | None) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        text = re.sub(r"\s+", " ", text).strip(" -|,.;")
        if not text:
            return None
        if len(text) > 120:
            text = text[:120].strip()
        return text or None

    @classmethod
    def _extract_company_from_resume_heuristic(cls, resume_text: str) -> str | None:
        lines = [line.strip() for line in str(resume_text or "").splitlines() if line.strip()]
        patterns = (
            re.compile(
                r"\b(?:at|@)\s+(?P<company>[A-Z][A-Za-z0-9&.,'()\- ]{1,100})",
                flags=re.IGNORECASE,
            ),
            re.compile(
                r"^\s*(?:company|current company|most recent company)\s*:\s*(?P<company>.+)$",
                flags=re.IGNORECASE,
            ),
        )
        for line in lines[:80]:
            for pattern in patterns:
                match = pattern.search(line)
                if not match:
                    continue
                company = cls._clean_company_name(match.group("company"))
                if company:
                    return company
        return None

    def infer_company_from_resume(
        self,
        *,
        request: ApplyRunRequest,
        min_confidence: float = 0.65,
    ) -> str | None:
        existing = self.resolve_typed_answer(request=request, question_key="current_company")
        if existing:
            return existing
        existing = self.resolve_typed_answer(request=request, question_key="most_recent_company")
        if existing:
            return existing

        profile_payload = request.profile_payload or {}
        resume_text = str(profile_payload.get("resume_text", "") or "").strip()
        if not resume_text:
            return None

        heuristic = self._extract_company_from_resume_heuristic(resume_text)
        if heuristic:
            return heuristic

        if not self.text_generator.enabled:
            return None

        prompt = (
            "Extract the candidate's current or most recent company from the resume text. "
            "Return JSON only with keys value, confidence, rationale. "
            "value must be a short company name string (not a sentence), and confidence must be 0..1.\n\n"
            f"{json.dumps({'resume_excerpt': resume_text[:2800]}, ensure_ascii=True)}"
        )
        payload = self.text_generator.generate_json(prompt=prompt)
        if not isinstance(payload, dict):
            return None
        value = self._clean_company_name(payload.get("value"))
        if not value:
            return None
        try:
            confidence = float(payload.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < float(min_confidence):
            return None
        return value

    @staticmethod
    def _application_profile(request: ApplyRunRequest) -> dict[str, Any]:
        profile_payload = request.profile_payload or {}
        application_profile = profile_payload.get("application_profile")
        return application_profile if isinstance(application_profile, dict) else {}

    def resolve_sensitive_answer(self, *, request: ApplyRunRequest, key: str) -> str:
        profile = self._application_profile(request)
        sensitive = profile.get("sensitive")
        if isinstance(sensitive, dict):
            value = sensitive.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "decline_to_answer"

    @staticmethod
    def _normalized_question_key(value: str | None) -> str:
        if not value:
            return ""
        lowered = normalize_text(value)
        lowered = re.sub(r"[^a-z0-9]+", "_", lowered)
        return lowered.strip("_")

    def _resolve_custom_answer(
        self,
        *,
        request: ApplyRunRequest,
        question_key: str,
        aliases: list[str] | None = None,
    ) -> str | None:
        normalized_key = self._normalized_question_key(question_key)
        normalized_aliases = {
            self._normalized_question_key(item) for item in (aliases or []) if item
        }
        normalized_aliases.add(normalized_key)
        profile = self._application_profile(request)
        custom_answers = profile.get("custom_answers")
        if not isinstance(custom_answers, list):
            return None
        for item in custom_answers:
            if not isinstance(item, dict):
                continue
            candidate_key = self._normalized_question_key(str(item.get("question_key", "")))
            if candidate_key in normalized_aliases:
                answer = str(item.get("answer", "")).strip()
                if answer:
                    return answer
        return None

    @staticmethod
    def classify_question(
        *,
        label: str | None = None,
        name: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        haystack_parts = [label or "", name or ""]
        if options:
            haystack_parts.extend(options)
        haystack = " ".join(haystack_parts).lower()

        if any(token in haystack for token in ["race", "ethnicity"]):
            return "race_ethnicity"
        if "gender" in haystack:
            return "gender"
        if "veteran" in haystack:
            return "veteran_status"
        if "disability" in haystack:
            return "disability_status"
        if "sponsor" in haystack:
            return "requires_sponsorship"
        if "authorization" in haystack or "authorized" in haystack:
            return "work_authorization"
        if "relocate" in haystack:
            return "willing_to_relocate"
        if any(token in haystack for token in ["cover letter", "essay", "why", "textarea"]):
            return "open_text"
        return "generic"

    def answer_question(
        self,
        *,
        request: ApplyRunRequest,
        label: str | None = None,
        name: str | None = None,
        options: list[str] | None = None,
    ) -> str:
        question_type = self.classify_question(label=label, name=name, options=options)
        if question_type in {
            "race_ethnicity",
            "gender",
            "veteran_status",
            "disability_status",
        }:
            return self.resolve_sensitive_answer(request=request, key=question_type)

        typed = self.resolve_typed_answer(
            request=request,
            question_key=question_type if question_type != "generic" else (name or label or ""),
        )
        if typed:
            return typed

        prompt = label or name or "Please provide a concise answer."
        return self.generate_open_text_answer(request=request, prompt=prompt)

    def choose_option_value(
        self,
        *,
        request: ApplyRunRequest,
        label: str | None,
        name: str | None,
        field_id: str | None,
        options: list[str],
        preferred_value: str | None = None,
        allow_llm_fallback: bool = False,
        min_confidence: float = 0.65,
    ) -> str | None:
        cleaned_options = [str(option).strip() for option in options if str(option).strip()]
        if not cleaned_options:
            return None

        preferred = str(preferred_value or "").strip()
        if preferred:
            direct = best_option_match(preferred, cleaned_options)
            if direct:
                return direct

        inferred_key = "_".join(
            token
            for token in re.split(
                r"[^a-z0-9]+",
                normalize_text(" ".join(part for part in [label or "", name or "", field_id or ""] if part)),
            )
            if token
        )
        typed = self.resolve_typed_answer(
            request=request,
            question_key=inferred_key or str(name or label or field_id or ""),
        )
        if typed:
            typed_match = best_option_match(typed, cleaned_options)
            if typed_match:
                return typed_match

        if self._should_prefer_decline_option(label=label, name=name, field_id=field_id):
            preferred_norm = normalize_text(preferred)
            if not preferred_norm or preferred_norm in {
                "decline_to_answer",
                "prefer_not_to_say",
                "not_listed",
            }:
                decline = self._normalized_decline_option(cleaned_options)
                if decline:
                    return decline

        if not allow_llm_fallback or not self.text_generator.enabled:
            return None

        profile_payload = request.profile_payload or {}
        profile = self._application_profile(request)
        context = {
            "question": {
                "label": label or "",
                "name": name or "",
                "field_id": field_id or "",
            },
            "options": cleaned_options,
            "preferred_value": preferred,
            "work_authorization": str(profile.get("work_authorization", "") or ""),
            "requires_sponsorship": bool(profile.get("requires_sponsorship")),
            "willing_to_relocate": bool(profile.get("willing_to_relocate")),
            "target_work_city": str(profile.get("target_work_city", "") or ""),
            "target_work_state": str(profile.get("target_work_state", "") or ""),
            "target_work_country": str(profile.get("target_work_country", "") or ""),
            "full_name": str(profile_payload.get("full_name", "") or ""),
            "resume_excerpt": str(profile_payload.get("resume_text", "") or "")[:1200],
        }
        prompt = (
            "Select the single best option for this job application field. "
            "Return JSON only with keys value, rationale, confidence. "
            "value must exactly match one provided option and confidence must be between 0 and 1.\n\n"
            f"{json.dumps(context, ensure_ascii=True)}"
        )
        candidate = self.text_generator.generate_json(prompt=prompt)
        if not isinstance(candidate, dict):
            return None
        model_value = str(candidate.get("value", "")).strip()
        if not model_value:
            return None
        try:
            confidence = float(candidate.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < float(min_confidence):
            return None
        return best_option_match(model_value, cleaned_options)

    def resolve_typed_answer(self, *, request: ApplyRunRequest, question_key: str) -> str | None:
        key = self._normalized_question_key(question_key)
        profile = self._application_profile(request)
        custom = self._resolve_custom_answer(request=request, question_key=key)
        if custom:
            return custom

        by_field = {
            "work_authorization": profile.get("work_authorization"),
            "requires_sponsorship": profile.get("requires_sponsorship"),
            "willing_to_relocate": profile.get("willing_to_relocate"),
            "years_experience": profile.get("years_experience"),
            "phone": profile.get("phone"),
            "city": profile.get("city"),
            "state": profile.get("state"),
            "country": profile.get("country"),
            "linkedin_url": profile.get("linkedin_url"),
            "github_url": profile.get("github_url"),
            "portfolio_url": profile.get("portfolio_url"),
            "current_company": profile.get("current_company"),
            "most_recent_company": profile.get("most_recent_company"),
            "current_title": profile.get("current_title"),
            "target_work_city": profile.get("target_work_city"),
            "target_work_state": profile.get("target_work_state"),
            "target_work_country": profile.get("target_work_country"),
        }
        if key in by_field:
            value = by_field[key]
            if isinstance(value, bool):
                return "yes" if value else "no"
            if value is not None and str(value).strip():
                return str(value).strip()

        if key in {"current_company", "most_recent_company"}:
            custom_company = self._resolve_custom_answer(
                request=request,
                question_key=key,
                aliases=[
                    "current_company",
                    "most_recent_company",
                    "latest_company",
                    "current_or_most_recent_company",
                    "company_name_current",
                ],
            )
            if custom_company:
                return custom_company
        if key == "current_title":
            custom_title = self._resolve_custom_answer(
                request=request,
                question_key=key,
                aliases=[
                    "current_title",
                    "most_recent_title",
                    "job_title_current",
                ],
            )
            if custom_title:
                return custom_title
        if key in {"target_work_city", "target_work_state", "target_work_country"}:
            custom_location = self._resolve_custom_answer(
                request=request,
                question_key=key,
                aliases=[
                    "target_work_city",
                    "target_work_state",
                    "target_work_country",
                    "target_city",
                    "target_state",
                    "target_country",
                    "preferred_city",
                    "preferred_state",
                    "preferred_country",
                ],
            )
            if custom_location:
                return custom_location

        if "gender" in key:
            return self.resolve_sensitive_answer(request=request, key="gender")
        if "race" in key or "ethnicity" in key:
            return self.resolve_sensitive_answer(request=request, key="race_ethnicity")
        if "veteran" in key:
            return self.resolve_sensitive_answer(request=request, key="veteran_status")
        if "disability" in key:
            return self.resolve_sensitive_answer(request=request, key="disability_status")

        return None

    def generate_open_text_answer(
        self,
        *,
        request: ApplyRunRequest,
        prompt: str,
    ) -> str:
        profile_payload = request.profile_payload or {}
        profile = self._application_profile(request)

        resume_text = str(profile_payload.get("resume_text", "")).strip()
        preferences = profile_payload.get("preferences")
        interests = []
        if isinstance(preferences, dict):
            raw_interests = preferences.get("interests")
            if isinstance(raw_interests, list):
                interests = [str(item).strip() for item in raw_interests if str(item).strip()]

        context = {
            "name": profile_payload.get("full_name", ""),
            "interests": interests,
            "writing_voice": profile.get("writing_voice", ""),
            "cover_letter_style": profile.get("cover_letter_style", ""),
            "achievements_summary": profile.get("achievements_summary", ""),
            "additional_context": profile.get("additional_context", ""),
            "resume_excerpt": resume_text[:3000],
            "question": prompt,
        }
        llm_prompt = (
            "You are writing concise and truthful application responses. "
            "Use only the provided profile context and avoid inventing facts. "
            "Return plain text only.\n\n"
            f"{json.dumps(context, ensure_ascii=True)}"
        )

        generated = self.text_generator.generate(prompt=llm_prompt)
        if generated:
            return generated

        name = str(profile_payload.get("full_name", "Candidate")).strip() or "Candidate"
        summary = str(profile.get("achievements_summary", "")).strip()
        interest_phrase = ", ".join(interests[:4]) if interests else "the role requirements"
        fallback = (
            f"I am {name}, and I am excited to contribute to this role. "
            f"My experience aligns well with {interest_phrase}."
        )
        if summary:
            fallback += f" Key highlight: {summary}"
        return fallback


__all__ = ["OpenAITextGenerator", "FormAnswerSynthesizer"]
