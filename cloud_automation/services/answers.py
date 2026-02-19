from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from ..models import ApplyRunRequest

logger = logging.getLogger(__name__)


class OpenAITextGenerator:
    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
        self.timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=self.timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def generate(self, *, prompt: str) -> str | None:
        if not self.enabled:
            return None

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
                    "temperature": 0.2,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except Exception:
            logger.exception("openai_generation_failed")
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

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


class FormAnswerSynthesizer:
    def __init__(self, *, text_generator: OpenAITextGenerator | None = None) -> None:
        self.text_generator = text_generator or OpenAITextGenerator()

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

    def resolve_typed_answer(self, *, request: ApplyRunRequest, question_key: str) -> str | None:
        key = question_key.strip().lower()
        profile = self._application_profile(request)

        custom_answers = profile.get("custom_answers")
        if isinstance(custom_answers, list):
            for item in custom_answers:
                if not isinstance(item, dict):
                    continue
                candidate_key = str(item.get("question_key", "")).strip().lower()
                if candidate_key == key:
                    answer = str(item.get("answer", "")).strip()
                    if answer:
                        return answer

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
        }
        if key in by_field:
            value = by_field[key]
            if isinstance(value, bool):
                return "yes" if value else "no"
            if value is not None and str(value).strip():
                return str(value).strip()

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
