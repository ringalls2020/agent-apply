from __future__ import annotations

import base64
import binascii
from contextlib import suppress
from dataclasses import dataclass
import json
import os
import re
from io import BytesIO

import httpx

MAX_RESUME_FILE_SIZE_BYTES = 8 * 1024 * 1024

RESUME_INTEREST_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("python", (r"\bpython\b",)),
    ("fastapi", (r"\bfastapi\b",)),
    ("sqlalchemy", (r"\bsqlalchemy\b",)),
    ("django", (r"\bdjango\b",)),
    ("flask", (r"\bflask\b",)),
    ("java", (r"\bjava\b",)),
    ("javascript", (r"\bjavascript\b", r"\bjs\b")),
    ("typescript", (r"\btypescript\b", r"\bts\b")),
    ("react", (r"\breact\b",)),
    ("nextjs", (r"\bnext\.?js\b",)),
    ("nodejs", (r"\bnode\.?js\b",)),
    ("graphql", (r"\bgraphql\b",)),
    ("rest-api", (r"\brest(?:ful)?\s+api\b", r"\brest\b")),
    ("sql", (r"\bsql\b",)),
    ("postgresql", (r"\bpostgres(?:ql)?\b",)),
    ("mysql", (r"\bmysql\b",)),
    ("mongodb", (r"\bmongodb\b", r"\bmongo\b")),
    ("redis", (r"\bredis\b",)),
    ("aws", (r"\baws\b", r"\bamazon web services\b")),
    ("gcp", (r"\bgcp\b", r"\bgoogle cloud\b")),
    ("azure", (r"\bazure\b",)),
    ("docker", (r"\bdocker\b",)),
    ("kubernetes", (r"\bkubernetes\b", r"\bk8s\b")),
    ("terraform", (r"\bterraform\b",)),
    ("ci-cd", (r"\bci/cd\b", r"\bci-cd\b", r"\bcontinuous integration\b")),
    ("devops", (r"\bdevops\b",)),
    ("ai", (r"\bartificial intelligence\b", r"\bai\b")),
    ("machine-learning", (r"\bmachine learning\b", r"\bml\b")),
    ("deep-learning", (r"\bdeep learning\b",)),
    ("nlp", (r"\bnlp\b", r"\bnatural language processing\b")),
    ("llm", (r"\bllm(?:s)?\b", r"\blarge language model(?:s)?\b")),
    ("data-science", (r"\bdata science\b", r"\bdata scientist\b")),
    ("data-engineering", (r"\bdata engineering\b", r"\bdata engineer\b")),
    ("mlops", (r"\bmlops\b",)),
    ("automation", (r"\bautomation\b",)),
    ("backend", (r"\bbackend\b", r"\bback-end\b")),
    ("frontend", (r"\bfrontend\b", r"\bfront-end\b")),
    ("full-stack", (r"\bfull stack\b", r"\bfull-stack\b")),
    ("security", (r"\bsecurity\b", r"\bcybersecurity\b")),
    ("robotics", (r"\brobotics\b",)),
    ("climate", (r"\bclimate\b",)),
)

RESUME_INTEREST_ALIAS_TO_CANONICAL: dict[str, str] = {
    "machine learning": "machine-learning",
    "artificial intelligence": "ai",
    "google cloud": "gcp",
    "amazon web services": "aws",
    "rest api": "rest-api",
    "full stack": "full-stack",
    "next.js": "nextjs",
    "node.js": "nodejs",
}

RESUME_NOISE_TOKENS = {
    "skills",
    "interests",
    "experience",
    "project",
    "projects",
    "summary",
    "professional",
    "engineer",
    "developer",
    "team",
    "work",
}


@dataclass(frozen=True)
class ResumeProfileExtraction:
    current_company: str | None = None
    most_recent_company: str | None = None
    current_title: str | None = None
    target_work_city: str | None = None
    target_work_state: str | None = None
    target_work_country: str | None = None


def sanitize_resume_text(value: str) -> str:
    # PostgreSQL TEXT rejects NUL bytes; binary uploads (PDF/DOCX) may include them.
    without_nul = value.replace("\x00", "")
    normalized = without_nul.replace("\r\n", "\n").replace("\r", "\n")
    scrubbed = "".join(
        char if char in {"\n", "\t"} or ord(char) >= 32 else " "
        for char in normalized
    )
    compacted = re.sub(r"[ \t]+", " ", scrubbed)
    compacted = re.sub(r"\n{3,}", "\n\n", compacted)
    return compacted.strip()


def decode_resume_file_content_base64(value: str) -> bytes:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Resume file payload is empty")
    try:
        decoded = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Resume file payload is not valid base64") from exc
    if not decoded:
        raise ValueError("Resume file payload is empty")
    if len(decoded) > MAX_RESUME_FILE_SIZE_BYTES:
        raise ValueError(
            f"Resume file exceeds {MAX_RESUME_FILE_SIZE_BYTES // (1024 * 1024)}MB limit"
        )
    return decoded


def extract_resume_text_from_file(
    *,
    filename: str,
    file_bytes: bytes,
    file_mime_type: str | None = None,
) -> str:
    normalized_filename = filename.strip().lower()
    normalized_mime = (file_mime_type or "").strip().lower()

    if normalized_filename.endswith((".txt", ".md")) or normalized_mime in {
        "text/plain",
        "text/markdown",
    }:
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("utf-8", errors="ignore")

    if normalized_filename.endswith(".pdf") or normalized_mime == "application/pdf":
        try:
            from pypdf import PdfReader
        except Exception as exc:
            raise ValueError("PDF parsing dependency is unavailable") from exc
        try:
            reader = PdfReader(BytesIO(file_bytes))
            pages = [str(page.extract_text() or "") for page in reader.pages]
            text = "\n".join(page for page in pages if page.strip())
            if text.strip():
                return text
            raise ValueError("Unable to extract text from PDF resume")
        except Exception as exc:
            raise ValueError("Unable to read PDF resume") from exc

    if normalized_filename.endswith(".doc") or normalized_mime == "application/msword":
        raise ValueError(
            "Legacy .doc resumes are not supported for text extraction. Use .docx, .pdf, .txt, or .md"
        )

    if normalized_filename.endswith(".docx") or normalized_mime == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ):
        try:
            from docx import Document
        except Exception as exc:
            raise ValueError("DOCX parsing dependency is unavailable") from exc
        try:
            document = Document(BytesIO(file_bytes))
            text = "\n".join(
                paragraph.text.strip()
                for paragraph in document.paragraphs
                if paragraph.text and paragraph.text.strip()
            )
            if text.strip():
                return text
            raise ValueError("Unable to extract text from DOCX resume")
        except Exception as exc:
            raise ValueError("Unable to read DOCX resume") from exc

    raise ValueError("Unsupported resume file type. Use .txt, .md, .pdf, or .docx")


def normalize_interest_token(token: str) -> str:
    normalized = token.strip().lower()
    normalized = re.sub(r"[^a-z0-9+#.\- ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return ""
    if normalized in RESUME_INTEREST_ALIAS_TO_CANONICAL:
        return RESUME_INTEREST_ALIAS_TO_CANONICAL[normalized]
    return normalized.replace(" ", "-")


def extract_resume_interests(resume_text: str, *, max_items: int = 15) -> list[str]:
    normalized = resume_text.lower()
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()

    for canonical, patterns in RESUME_INTEREST_PATTERNS:
        first_index: int | None = None
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match is not None:
                first_index = match.start() if first_index is None else min(first_index, match.start())
        if first_index is None:
            continue
        ranked.append((first_index, canonical))
        seen.add(canonical)

    for section_match in re.finditer(
        r"(?:skills?|interests?)\s*[:\-]\s*([^\n]{1,300})",
        normalized,
    ):
        segment = section_match.group(1)
        for candidate in re.split(r"[,/;|]", segment):
            normalized_token = normalize_interest_token(candidate)
            if (
                not normalized_token
                or normalized_token in seen
                or normalized_token in RESUME_NOISE_TOKENS
                or len(normalized_token) < 2
                or len(normalized_token) > 40
            ):
                continue
            ranked.append((section_match.start(), normalized_token))
            seen.add(normalized_token)

    ranked.sort(key=lambda entry: entry[0])
    return [interest for _, interest in ranked[:max_items]]


def _clean_resume_fact(value: str | None, *, max_length: int = 120) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip(" -|,")
    if not text:
        return None
    if len(text) > max_length:
        return text[:max_length].strip()
    return text


def _parse_location_triplet(raw: str) -> tuple[str | None, str | None, str | None]:
    cleaned = _clean_resume_fact(raw, max_length=140)
    if not cleaned:
        return None, None, None
    value = cleaned.replace("Remote", "").replace("remote", "").strip(" -,")
    if not value:
        return None, None, None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) >= 3:
        return (
            _clean_resume_fact(parts[0], max_length=80),
            _clean_resume_fact(parts[1], max_length=80),
            _clean_resume_fact(parts[2], max_length=80),
        )
    if len(parts) == 2:
        return (
            _clean_resume_fact(parts[0], max_length=80),
            None,
            _clean_resume_fact(parts[1], max_length=80),
        )
    return None, None, _clean_resume_fact(parts[0], max_length=80)


def _extract_company_title_from_lines(lines: list[str]) -> tuple[str | None, str | None]:
    title: str | None = None
    company: str | None = None

    at_pattern = re.compile(
        r"(?P<title>[A-Za-z][A-Za-z0-9/&,.()' \-]{2,90})\s+(?:at|@)\s+(?P<company>[A-Za-z][A-Za-z0-9&,.()' \-]{2,90})"
    )
    split_pattern = re.compile(
        r"(?P<company>[A-Za-z][A-Za-z0-9&,.()' \-]{2,90})\s*[|\-]\s*(?P<title>[A-Za-z][A-Za-z0-9/&,.()' \-]{2,90})"
    )

    for line in lines[:80]:
        if not line:
            continue
        if title is None or company is None:
            match = at_pattern.search(line)
            if match:
                title = _clean_resume_fact(match.group("title"))
                company = _clean_resume_fact(match.group("company"))
                if title or company:
                    break
        if title is None or company is None:
            match = split_pattern.search(line)
            if match:
                title = _clean_resume_fact(match.group("title"))
                company = _clean_resume_fact(match.group("company"))
                if title or company:
                    break

    if title is None or company is None:
        for line in lines[:120]:
            lowered = line.lower()
            if company is None and "company" in lowered and ":" in line:
                _, rhs = line.split(":", 1)
                company = _clean_resume_fact(rhs)
            if title is None and ("title" in lowered or "role" in lowered) and ":" in line:
                _, rhs = line.split(":", 1)
                title = _clean_resume_fact(rhs)
            if title and company:
                break

    return title, company


def _extract_target_work_location_from_lines(
    lines: list[str],
) -> tuple[str | None, str | None, str | None]:
    location_hints = (
        "location",
        "located in",
        "based in",
        "target location",
        "current location",
    )
    for line in lines[:120]:
        lowered = line.lower()
        if not any(token in lowered for token in location_hints):
            continue
        value = line
        if ":" in line:
            _, value = line.split(":", 1)
        city, state, country = _parse_location_triplet(value)
        if city or state or country:
            return city, state, country

    for line in lines[:80]:
        if "," not in line:
            continue
        city, state, country = _parse_location_triplet(line)
        if city or state or country:
            return city, state, country
    return None, None, None


def _extract_json_object(raw: str) -> dict[str, object] | None:
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


def _extract_with_llm(resume_text: str) -> ResumeProfileExtraction | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    if os.getenv("RESUME_PROFILE_LLM_FALLBACK_ENABLED", "true").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))
    payload = {
        "model": model,
        "input": (
            "Extract applicant profile facts from this resume text. Return JSON only with keys: "
            "current_company, most_recent_company, current_title, target_work_city, target_work_state, target_work_country. "
            "Use null when unknown.\n\n"
            + resume_text[:3500]
        ),
        "max_output_tokens": 220,
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
    output_text = str(body.get("output_text", "")).strip()
    if not output_text:
        output = body.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                        output_text = str(block.get("text", "")).strip()
                        if output_text:
                            break
                if output_text:
                    break
    decoded = _extract_json_object(output_text)
    if not isinstance(decoded, dict):
        return None
    return ResumeProfileExtraction(
        current_company=_clean_resume_fact(decoded.get("current_company")),
        most_recent_company=_clean_resume_fact(decoded.get("most_recent_company")),
        current_title=_clean_resume_fact(decoded.get("current_title")),
        target_work_city=_clean_resume_fact(decoded.get("target_work_city")),
        target_work_state=_clean_resume_fact(decoded.get("target_work_state")),
        target_work_country=_clean_resume_fact(decoded.get("target_work_country")),
    )


def extract_resume_profile_facts(resume_text: str) -> ResumeProfileExtraction:
    sanitized = sanitize_resume_text(resume_text)
    if not sanitized:
        return ResumeProfileExtraction()
    lines = [line.strip() for line in sanitized.splitlines() if line.strip()]
    title, company = _extract_company_title_from_lines(lines)
    location_city, location_state, location_country = _extract_target_work_location_from_lines(lines)
    extracted = ResumeProfileExtraction(
        current_company=company,
        most_recent_company=company,
        current_title=title,
        target_work_city=location_city,
        target_work_state=location_state,
        target_work_country=location_country,
    )
    if all(
        getattr(extracted, field) is not None
        for field in (
            "current_company",
            "most_recent_company",
            "current_title",
            "target_work_city",
            "target_work_state",
            "target_work_country",
        )
    ):
        return extracted
    with suppress(Exception):
        llm = _extract_with_llm(sanitized)
        if llm is not None:
            return ResumeProfileExtraction(
                current_company=extracted.current_company or llm.current_company,
                most_recent_company=extracted.most_recent_company or llm.most_recent_company,
                current_title=extracted.current_title or llm.current_title,
                target_work_city=extracted.target_work_city or llm.target_work_city,
                target_work_state=extracted.target_work_state or llm.target_work_state,
                target_work_country=extracted.target_work_country or llm.target_work_country,
            )
    return extracted


__all__ = [
    "ResumeProfileExtraction",
    "sanitize_resume_text",
    "extract_resume_interests",
    "extract_resume_profile_facts",
    "normalize_interest_token",
    "decode_resume_file_content_base64",
    "extract_resume_text_from_file",
]
