from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlsplit, urlunsplit


GREENHOUSE_EMBED_PATTERN = re.compile(
    r"boards\.greenhouse\.io/embed/job_board/js\?for=([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
GREENHOUSE_HOSTED_PATTERN = re.compile(
    r"boards\.greenhouse\.io/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
LEVER_HOSTED_PATTERN = re.compile(
    r"jobs\.lever\.co/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
SMARTRECRUITERS_API_PATTERN = re.compile(
    r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)/postings",
    re.IGNORECASE,
)

_GREENHOUSE_RESERVED_SEGMENTS = {
    "embed",
    "jobs",
    "job_app",
    "api",
    "job_board",
    "js",
}


@dataclass(frozen=True)
class ExtractedToken:
    provider: str
    token: str


@dataclass(frozen=True)
class JobIdentity:
    provider: str
    provider_token: str | None
    provider_job_id: str | None
    normalized_apply_url: str
    normalized_apply_url_hash: str
    canonical_key: str


def _normalize_token(value: str) -> str:
    return value.strip().strip("/").lower()


def extract_ats_tokens_from_text(text: str) -> set[ExtractedToken]:
    decoded = html.unescape(text or "")
    matches: set[ExtractedToken] = set()

    for raw in GREENHOUSE_EMBED_PATTERN.findall(decoded):
        token = _normalize_token(raw)
        if token:
            matches.add(ExtractedToken(provider="greenhouse", token=token))

    for raw in GREENHOUSE_HOSTED_PATTERN.findall(decoded):
        token = _normalize_token(raw)
        if token and token not in _GREENHOUSE_RESERVED_SEGMENTS:
            matches.add(ExtractedToken(provider="greenhouse", token=token))

    for raw in LEVER_HOSTED_PATTERN.findall(decoded):
        token = _normalize_token(raw)
        if token:
            matches.add(ExtractedToken(provider="lever", token=token))

    for raw in SMARTRECRUITERS_API_PATTERN.findall(decoded):
        token = _normalize_token(raw)
        if token:
            matches.add(ExtractedToken(provider="smartrecruiters", token=token))

    return matches


def extract_ats_tokens_from_values(values: Iterable[str]) -> set[ExtractedToken]:
    tokens: set[ExtractedToken] = set()
    for value in values:
        tokens.update(extract_ats_tokens_from_text(value))
    return tokens


def _normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = parts.query
    return urlunsplit((scheme, netloc, path, query, ""))


def _infer_provider_token_from_url(provider: str, apply_url: str) -> tuple[str | None, str | None]:
    parts = urlsplit(apply_url)
    host = (parts.hostname or "").lower()
    segments = [item for item in parts.path.split("/") if item]
    query = parse_qs(parts.query)

    if provider == "greenhouse" and "boards.greenhouse.io" in host:
        token = segments[0].lower() if segments else None
        job_id = query.get("gh_jid", [None])[0]
        if not job_id and len(segments) >= 3 and segments[1].lower() == "jobs":
            job_id = segments[2]
        return token, job_id

    if provider == "lever" and "jobs.lever.co" in host:
        token = segments[0].lower() if segments else None
        job_id = segments[1] if len(segments) >= 2 else None
        return token, job_id

    if provider == "smartrecruiters" and "jobs.smartrecruiters.com" in host:
        token = segments[0].lower() if segments else None
        job_id = segments[1] if len(segments) >= 2 else None
        return token, job_id

    return None, None


def _infer_provider_job_id_from_external_id(
    provider: str,
    provider_token: str | None,
    external_job_id: str | None,
) -> str | None:
    if not external_job_id:
        return None
    prefix = f"{provider}-"
    if external_job_id.startswith(prefix):
        remainder = external_job_id[len(prefix) :]
        if provider_token:
            token_prefix = f"{provider_token}-"
            if remainder.startswith(token_prefix):
                return remainder[len(token_prefix) :]
        return remainder
    return external_job_id


def build_job_identity(
    *,
    source: str,
    apply_url: str,
    external_job_id: str | None = None,
) -> JobIdentity:
    provider = source.strip().lower()
    normalized_url = _normalize_url(apply_url)
    url_hash = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()

    provider_token, provider_job_id = _infer_provider_token_from_url(provider, normalized_url)
    if not provider_job_id:
        provider_job_id = _infer_provider_job_id_from_external_id(
            provider=provider,
            provider_token=provider_token,
            external_job_id=external_job_id,
        )

    if provider_token and provider_job_id:
        canonical_key = f"{provider}:{provider_token}:{provider_job_id}"
    else:
        canonical_key = f"{provider}:{url_hash}"

    return JobIdentity(
        provider=provider,
        provider_token=provider_token,
        provider_job_id=provider_job_id,
        normalized_apply_url=normalized_url,
        normalized_apply_url_hash=url_hash,
        canonical_key=canonical_key,
    )
