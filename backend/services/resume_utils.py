from __future__ import annotations

import base64
import binascii
import re
from io import BytesIO

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


__all__ = [
    "sanitize_resume_text",
    "extract_resume_interests",
    "normalize_interest_token",
    "decode_resume_file_content_base64",
    "extract_resume_text_from_file",
]
