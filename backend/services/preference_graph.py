from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


_TOKEN_PATTERN = re.compile(r"[a-z0-9+#]{2,}")
_STOPWORDS = {
    "and",
    "the",
    "with",
    "for",
    "from",
    "that",
    "this",
    "your",
    "their",
    "have",
    "has",
    "are",
    "was",
    "were",
    "job",
    "role",
    "work",
    "team",
    "using",
    "experience",
    "strong",
}
_ROLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bsoftware engineer\b", "software-engineer"),
    (r"\bbackend engineer\b", "backend-engineer"),
    (r"\bfront(?:end| end) engineer\b", "frontend-engineer"),
    (r"\bfull(?: |-)?stack engineer\b", "full-stack-engineer"),
    (r"\bmachine learning engineer\b", "ml-engineer"),
    (r"\bdata engineer\b", "data-engineer"),
    (r"\bdevops engineer\b", "devops-engineer"),
    (r"\bsite reliability engineer\b", "sre"),
)
_DOMAIN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bfintech\b", "fintech"),
    (r"\bhealth(?:care|tech)?\b", "healthtech"),
    (r"\bclimate(?:tech)?\b", "climate"),
    (r"\bcybersecurity\b", "cybersecurity"),
    (r"\be-?commerce\b", "ecommerce"),
    (r"\bedtech\b", "edtech"),
)
_LOCATION_PATTERNS: tuple[tuple[str, str, bool], ...] = (
    (r"\bremote\b", "remote", True),
    (r"\bhybrid\b", "hybrid", False),
    (r"\bonsite\b", "onsite", False),
    (r"\bunited states\b|\busa\b", "united-states", True),
    (r"\bcanada\b", "canada", True),
    (r"\bunited kingdom\b|\buk\b", "united-kingdom", True),
    (r"\beurope\b|\beu\b", "europe", True),
)
_WORK_AUTH_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bvisa sponsorship\b", "visa-sponsorship"),
    (r"\bno sponsorship\b", "no-sponsorship"),
    (r"\bauthorized to work in (?:the )?united states\b", "us-work-authorized"),
    (r"\bauthorized to work in canada\b", "ca-work-authorized"),
)
_COMPENSATION_PATTERN = re.compile(
    r"\$?\s*(\d{2,3})(?:,\d{3})?(?:\s*-\s*\$?\s*(\d{2,3})(?:,\d{3})?)?\s*[kK]\b"
)
_SOURCE_MULTIPLIER = {
    "manual": 1.0,
    "resume_parse": 0.72,
    "behavioral": 0.85,
    "import": 0.8,
}


@dataclass(frozen=True)
class PreferenceHypothesis:
    node_type: str
    canonical_key: str
    label: str
    source: str
    confidence: float
    weight: float
    hard_constraint: bool = False
    relationship: str = "prefers"
    span_ref: str | None = None
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphPreferenceEdge:
    node_type: str
    canonical_key: str
    label: str
    source: str
    confidence: float
    weight: float
    hard_constraint: bool
    relationship: str


@dataclass(frozen=True)
class MatchScoreBreakdown:
    filtered_out: bool
    graph_score: float
    semantic_score: float
    final_score: float
    matched_nodes: tuple[str, ...]
    inferred_nodes: tuple[str, ...]
    manual_override_nodes: tuple[str, ...]
    constraint_reasons: tuple[str, ...]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _normalize_key(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9+# ]+", "-", lowered)
    lowered = re.sub(r"-{2,}", "-", lowered).strip("-")
    return lowered


def _add_hypothesis(
    hypotheses: dict[tuple[str, str, str], PreferenceHypothesis],
    hypothesis: PreferenceHypothesis,
) -> None:
    key = (
        hypothesis.node_type,
        hypothesis.canonical_key,
        hypothesis.relationship,
    )
    existing = hypotheses.get(key)
    if existing is None or hypothesis.confidence > existing.confidence:
        hypotheses[key] = hypothesis


def extract_resume_preference_hypotheses(
    *,
    resume_text: str,
    parsed_interests: Sequence[str] | None = None,
) -> list[PreferenceHypothesis]:
    normalized = resume_text.lower()
    hypotheses: dict[tuple[str, str, str], PreferenceHypothesis] = {}

    for interest in parsed_interests or []:
        canonical = _normalize_key(interest)
        if not canonical:
            continue
        _add_hypothesis(
            hypotheses,
            PreferenceHypothesis(
                node_type="skill",
                canonical_key=canonical,
                label=interest,
                source="resume_parse",
                confidence=0.74,
                weight=0.65,
                rationale="derived_from_resume_skills_or_interest_section",
            ),
        )

    for pattern, canonical in _ROLE_PATTERNS:
        match = re.search(pattern, normalized)
        if match is None:
            continue
        _add_hypothesis(
            hypotheses,
            PreferenceHypothesis(
                node_type="role",
                canonical_key=canonical,
                label=canonical.replace("-", " ").title(),
                source="resume_parse",
                confidence=0.72,
                weight=0.6,
                span_ref=f"{match.start()}:{match.end()}",
                rationale="role_title_detected_in_resume",
            ),
        )

    for pattern, canonical in _DOMAIN_PATTERNS:
        match = re.search(pattern, normalized)
        if match is None:
            continue
        _add_hypothesis(
            hypotheses,
            PreferenceHypothesis(
                node_type="domain",
                canonical_key=canonical,
                label=canonical.replace("-", " ").title(),
                source="resume_parse",
                confidence=0.65,
                weight=0.52,
                span_ref=f"{match.start()}:{match.end()}",
                rationale="industry_or_domain_term_detected",
            ),
        )

    for pattern, canonical, hard_constraint in _LOCATION_PATTERNS:
        match = re.search(pattern, normalized)
        if match is None:
            continue
        _add_hypothesis(
            hypotheses,
            PreferenceHypothesis(
                node_type="location",
                canonical_key=canonical,
                label=canonical.replace("-", " ").title(),
                source="resume_parse",
                confidence=0.56,
                weight=0.45,
                hard_constraint=hard_constraint and canonical in {"remote", "hybrid", "onsite"},
                span_ref=f"{match.start()}:{match.end()}",
                rationale="location_signal_detected_in_resume",
            ),
        )

    for pattern, canonical in _WORK_AUTH_PATTERNS:
        match = re.search(pattern, normalized)
        if match is None:
            continue
        _add_hypothesis(
            hypotheses,
            PreferenceHypothesis(
                node_type="work_auth",
                canonical_key=canonical,
                label=canonical.replace("-", " ").title(),
                source="resume_parse",
                confidence=0.58,
                weight=0.5,
                hard_constraint=True,
                span_ref=f"{match.start()}:{match.end()}",
                rationale="work_authorization_signal_detected",
            ),
        )

    for match in _COMPENSATION_PATTERN.finditer(normalized):
        min_salary_k = int(match.group(1))
        max_salary_k = int(match.group(2) or match.group(1))
        min_salary = min(min_salary_k, max_salary_k) * 1000
        max_salary = max(min_salary_k, max_salary_k) * 1000
        _add_hypothesis(
            hypotheses,
            PreferenceHypothesis(
                node_type="compensation",
                canonical_key=f"base-usd-{min_salary}-{max_salary}",
                label=f"${min_salary:,}-${max_salary:,} base",
                source="resume_parse",
                confidence=0.42,
                weight=0.35,
                span_ref=f"{match.start()}:{match.end()}",
                rationale="compensation_range_detected",
                metadata={"currency": "USD", "base_min": min_salary, "base_max": max_salary},
            ),
        )
        break

    return sorted(
        hypotheses.values(),
        key=lambda item: (
            item.node_type,
            item.canonical_key,
            item.relationship,
        ),
    )


def tokenize_for_semantics(text: str) -> list[str]:
    tokens: list[str] = []
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if token in _STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def build_sparse_semantic_vector(text: str, *, max_features: int = 128) -> dict[str, float]:
    counts: dict[str, int] = {}
    for token in tokenize_for_semantics(text):
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return {}
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:max_features]
    total = float(sum(count for _, count in ranked))
    if total <= 0:
        return {}
    return {token: count / total for token, count in ranked}


def cosine_similarity(vec_a: Mapping[str, float], vec_b: Mapping[str, float]) -> float:
    if not vec_a or not vec_b:
        return 0.0
    dot = 0.0
    for token, value in vec_a.items():
        dot += value * float(vec_b.get(token, 0.0))
    norm_a = math.sqrt(sum(value * value for value in vec_a.values()))
    norm_b = math.sqrt(sum(value * value for value in vec_b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return _clamp01(dot / (norm_a * norm_b))


def _edge_matches_text(edge: GraphPreferenceEdge, normalized_text: str) -> bool:
    key = edge.canonical_key.replace("-", " ").strip().lower()
    label = edge.label.strip().lower()
    if key and key in normalized_text:
        return True
    if label and label in normalized_text:
        return True
    return False


def evaluate_match_with_graph(
    *,
    title: str,
    company: str,
    location: str | None,
    reason: str,
    base_score: float,
    edges: Sequence[GraphPreferenceEdge],
    profile_vector: Mapping[str, float] | None,
) -> MatchScoreBreakdown:
    normalized_text = " ".join(
        item.strip().lower()
        for item in [title, company, location or "", reason]
        if isinstance(item, str)
    )
    hard_location_edges = [
        edge
        for edge in edges
        if edge.node_type == "location"
        and edge.hard_constraint
        and edge.relationship in {"prefers", "overrides"}
    ]
    if hard_location_edges:
        location_value = (location or "").strip().lower()
        location_matched = any(
            edge.canonical_key.replace("-", " ") in location_value
            or edge.label.strip().lower() in location_value
            for edge in hard_location_edges
        )
        if not location_matched:
            return MatchScoreBreakdown(
                filtered_out=True,
                graph_score=0.0,
                semantic_score=0.0,
                final_score=0.0,
                matched_nodes=tuple(),
                inferred_nodes=tuple(),
                manual_override_nodes=tuple(),
                constraint_reasons=(
                    "Rejected by hard location preference constraint",
                ),
            )

    matched_nodes: list[str] = []
    inferred_nodes: list[str] = []
    manual_override_nodes: list[str] = []
    constraint_reasons: list[str] = []
    matched_signal = 0.0
    total_signal = 0.0
    conflict_penalty = 0.0
    hard_skill_misses = 0

    for edge in edges:
        multiplier = _SOURCE_MULTIPLIER.get(edge.source, 0.6)
        potential = max(0.05, edge.weight * edge.confidence * multiplier)
        if edge.relationship == "conflicts_with":
            if _edge_matches_text(edge, normalized_text):
                conflict_penalty += potential
                constraint_reasons.append(f"Conflict with excluded preference: {edge.label}")
            continue

        total_signal += potential
        matched = _edge_matches_text(edge, normalized_text)
        if matched:
            matched_signal += potential
            matched_nodes.append(edge.label)
            if edge.source == "resume_parse":
                inferred_nodes.append(edge.label)
            if edge.source == "manual" and edge.relationship == "overrides":
                manual_override_nodes.append(edge.label)
        elif edge.hard_constraint and edge.node_type == "skill":
            hard_skill_misses += 1

    signal_ratio = matched_signal / total_signal if total_signal > 0 else 0.0
    penalty = min(0.6, (conflict_penalty * 0.55) + (hard_skill_misses * 0.08))
    graph_score = _clamp01((0.35 * _clamp01(base_score)) + (0.65 * signal_ratio) - penalty)

    semantic_vector = build_sparse_semantic_vector(normalized_text)
    semantic_score = cosine_similarity(profile_vector or {}, semantic_vector)

    final_score = _clamp01((0.55 * graph_score) + (0.30 * semantic_score) + (0.15 * _clamp01(base_score)))
    if hard_location_edges:
        constraint_reasons.append("Hard location preference satisfied")

    return MatchScoreBreakdown(
        filtered_out=False,
        graph_score=graph_score,
        semantic_score=semantic_score,
        final_score=final_score,
        matched_nodes=tuple(dict.fromkeys(matched_nodes)),
        inferred_nodes=tuple(dict.fromkeys(inferred_nodes)),
        manual_override_nodes=tuple(dict.fromkeys(manual_override_nodes)),
        constraint_reasons=tuple(dict.fromkeys(constraint_reasons)),
    )


__all__ = [
    "GraphPreferenceEdge",
    "MatchScoreBreakdown",
    "PreferenceHypothesis",
    "build_sparse_semantic_vector",
    "cosine_similarity",
    "evaluate_match_with_graph",
    "extract_resume_preference_hypotheses",
]
