from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from common.time import utc_now


_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class FormOptionSnapshot:
    label: str
    value: str
    selected: bool = False


@dataclass(frozen=True)
class FormFieldSnapshot:
    dom_path: str
    tag_name: str
    input_type: str
    role: str
    required: bool
    visible: bool
    enabled: bool
    label: str
    helper_text: str
    error_text: str
    placeholder: str
    name: str
    field_id: str
    value: str
    section_heading: str
    options: tuple[FormOptionSnapshot, ...] = field(default_factory=tuple)
    intent: str = "short_fact_text"
    widget_type: str = "unknown"
    constraint_mode: str = "free_text"
    file_slot: str | None = None

    @property
    def semantic_key(self) -> str:
        return semantic_key_from_parts(
            label=self.label,
            name=self.name,
            field_id=self.field_id,
            helper_text=self.helper_text,
            option_labels=[option.label for option in self.options],
        )


@dataclass(frozen=True)
class FormSnapshot:
    captured_at: str
    fields: tuple[FormFieldSnapshot, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FieldFillAttempt:
    semantic_key: str
    label: str
    adapter: str
    attempted_value: str
    success: bool
    verified: bool
    reason: str | None = None
    intent: str | None = None
    widget_type: str | None = None
    constraint_mode: str | None = None
    failure_category: str | None = None
    file_slot: str | None = None


@dataclass(frozen=True)
class FormFillDiagnostics:
    run_stage: str
    detected_field_count: int
    filled_count: int
    unresolved_required: tuple[str, ...] = field(default_factory=tuple)
    attempts: tuple[FieldFillAttempt, ...] = field(default_factory=tuple)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", value).strip().lower()
    return collapsed


def normalize_key(value: str | None) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    normalized = _NON_ALNUM_RE.sub("_", normalized).strip("_")
    return normalized


def tokenize(value: str | None) -> set[str]:
    normalized = normalize_text(value)
    if not normalized:
        return set()
    return {token for token in _NON_ALNUM_RE.split(normalized) if token}


def semantic_key_from_parts(
    *,
    label: str | None,
    name: str | None,
    field_id: str | None,
    helper_text: str | None,
    option_labels: list[str] | None,
) -> str:
    option_bits = " ".join(option_labels or [])
    haystack = " ".join(
        part for part in [label or "", name or "", field_id or "", helper_text or "", option_bits] if part
    )
    key = normalize_key(haystack)
    return key or "unknown_field"


def _is_yes_alias(value: str) -> bool:
    normalized = normalize_text(value)
    return normalized in {
        "yes",
        "y",
        "true",
        "1",
        "authorized",
        "authorized to work",
        "i am authorized",
        "eligible",
        "eligible to work",
        "affirmative",
    }


def _is_no_alias(value: str) -> bool:
    normalized = normalize_text(value)
    return normalized in {
        "no",
        "n",
        "false",
        "0",
        "not authorized",
        "negative",
    }


def is_placeholder_value(value: str | None) -> bool:
    normalized = normalize_text(value)
    return normalized in {
        "",
        "select",
        "select...",
        "choose",
        "choose...",
        "please select",
        "please choose",
    }


def best_option_match(preferred_value: str, options: list[str]) -> str | None:
    cleaned_options = [str(option).strip() for option in options if str(option).strip()]
    if not preferred_value.strip() or not cleaned_options:
        return None

    normalized_preferred = normalize_text(preferred_value)
    for option in cleaned_options:
        if normalize_text(option) == normalized_preferred:
            return option

    if _is_yes_alias(preferred_value):
        for option in cleaned_options:
            if _is_yes_alias(option):
                return option
    if _is_no_alias(preferred_value):
        for option in cleaned_options:
            if _is_no_alias(option):
                return option

    preferred_tokens = tokenize(preferred_value)
    best: tuple[float, str] | None = None
    for option in cleaned_options:
        option_tokens = tokenize(option)
        if not option_tokens:
            continue
        overlap = len(preferred_tokens.intersection(option_tokens))
        union = len(preferred_tokens.union(option_tokens))
        if union <= 0:
            continue
        score = overlap / union
        if best is None or score > best[0]:
            best = (score, option)
    if best is not None and best[0] >= 0.25:
        return best[1]
    return None


def classify_widget_type(
    *,
    tag_name: str | None,
    input_type: str | None,
    role: str | None,
) -> str:
    tag = normalize_text(tag_name)
    kind = normalize_text(input_type)
    aria_role = normalize_text(role)
    if kind == "file":
        return "file_upload"
    if tag == "select":
        return "native_select"
    if aria_role == "combobox":
        return "custom_combobox"
    if aria_role == "listbox":
        return "listbox"
    if kind == "radio":
        return "radio_group"
    if kind == "checkbox":
        return "checkbox"
    if tag == "textarea":
        return "textarea"
    if tag == "input":
        return "text_input"
    if aria_role:
        return aria_role
    return "unknown"


def is_option_constrained(
    *,
    tag_name: str | None,
    input_type: str | None,
    role: str | None,
) -> bool:
    tag = normalize_text(tag_name)
    kind = normalize_text(input_type)
    aria_role = normalize_text(role)
    return (
        tag == "select"
        or aria_role in {"combobox", "listbox"}
        or kind in {"radio", "checkbox"}
    )


def classify_file_slot(
    *,
    label: str | None,
    name: str | None,
    field_id: str | None,
    helper_text: str | None,
) -> str | None:
    haystack = normalize_text(
        " ".join(
            part
            for part in [label or "", name or "", field_id or "", helper_text or ""]
            if part
        )
    )
    if not haystack:
        return None
    if "cover" in haystack and "letter" in haystack:
        return "cover_letter"
    if "resume" in haystack or "cv" in haystack:
        return "resume"
    if "attachment" in haystack or "upload" in haystack:
        return "other"
    return None


def should_ignore_internal_field_candidate(
    *,
    label: str | None,
    name: str | None,
    field_id: str | None,
    tag_name: str | None,
    input_type: str | None,
    role: str | None,
) -> bool:
    normalized_label = normalize_text(label)
    normalized_name = normalize_text(name)
    normalized_id = normalize_text(field_id)
    normalized_tag = normalize_text(tag_name)
    normalized_type = normalize_text(input_type)
    normalized_role = normalize_text(role)

    if not normalized_label and not normalized_name and not normalized_id:
        return True

    react_internal_tokens = (
        "react-select",
        "downshift",
        "typeahead",
        "combobox-input",
    )
    identity = f"{normalized_name} {normalized_id}"
    if (
        normalized_tag == "input"
        and normalized_role in {"combobox", "listbox", ""}
        and not normalized_label
        and any(token in identity for token in react_internal_tokens)
    ):
        return True

    if (
        normalized_tag == "input"
        and normalized_type in {"hidden", "search"}
        and not normalized_label
        and not normalized_name
        and not normalized_id
    ):
        return True

    return False


def _is_yes_no_options(options: list[str]) -> bool:
    if not options:
        return False
    normalized = [normalize_text(item) for item in options if normalize_text(item)]
    if not normalized:
        return False
    yes_aliases = {"yes", "y", "true", "1"}
    no_aliases = {"no", "n", "false", "0"}
    has_yes = any(item in yes_aliases for item in normalized)
    has_no = any(item in no_aliases for item in normalized)
    return has_yes and has_no


def classify_field_intent(
    *,
    label: str | None,
    name: str | None,
    field_id: str | None,
    helper_text: str | None,
    tag_name: str | None,
    input_type: str | None,
    role: str | None,
    options: list[str] | None = None,
) -> str:
    tag = normalize_text(tag_name)
    kind = normalize_text(input_type)
    widget = classify_widget_type(tag_name=tag, input_type=kind, role=role)
    haystack = normalize_text(
        " ".join(
            part
            for part in [label or "", name or "", field_id or "", helper_text or ""]
            if part
        )
    )
    if widget == "file_upload":
        return "file_upload"
    normalized_options = [str(item).strip() for item in (options or []) if str(item).strip()]
    if is_option_constrained(tag_name=tag, input_type=kind, role=role):
        if _is_yes_no_options(normalized_options):
            return "binary"
        if any(
            token in haystack
            for token in [
                "authorized",
                "authorization",
                "sponsor",
                "relocate",
                "consent",
                "agree",
            ]
        ):
            return "binary"
        return "option_constrained"
    long_form_tokens = [
        "cover letter",
        "why do you",
        "essay",
        "additional information",
        "anything else",
        "3-4 sentences",
        "paragraph",
        "describe",
    ]
    if tag == "textarea" or any(token in haystack for token in long_form_tokens):
        return "long_form_text"
    return "short_fact_text"


def group_fields_by_semantic_key(fields: list[FormFieldSnapshot]) -> dict[str, list[FormFieldSnapshot]]:
    grouped: dict[str, list[FormFieldSnapshot]] = {}
    for field in fields:
        grouped.setdefault(field.semantic_key, []).append(field)
    return grouped


async def capture_form_snapshot(page: Any, *, max_fields: int = 250) -> FormSnapshot:
    if not hasattr(page, "evaluate"):
        return FormSnapshot(captured_at=utc_now().isoformat(), fields=())
    try:
        rows = await page.evaluate(
            """(limit) => {
                const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
                const isVisible = (el) => {
                    const style = window.getComputedStyle(el);
                    if (style.visibility === "hidden" || style.display === "none") return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const cssEscape = (value) => {
                    if (window.CSS && typeof window.CSS.escape === "function") {
                        return window.CSS.escape(value);
                    }
                    return String(value).replace(/[^a-zA-Z0-9_\\-]/g, "\\\\$&");
                };
                const buildDomPath = (el) => {
                    const id = normalize(el.id);
                    if (id) return "#" + cssEscape(id);
                    const name = normalize(el.getAttribute("name"));
                    const tag = String(el.tagName || "input").toLowerCase();
                    if (name) return `${tag}[name="${name}"]`;
                    const parent = el.parentElement;
                    if (!parent) return tag;
                    const siblings = Array.from(parent.children).filter((child) => child.tagName === el.tagName);
                    const index = siblings.indexOf(el);
                    return `${tag}:nth-of-type(${Math.max(index + 1, 1)})`;
                };
                const findLabel = (el) => {
                    const id = normalize(el.id);
                    if (id) {
                        const byFor = document.querySelector(`label[for="${cssEscape(id)}"]`);
                        if (byFor) return normalize(byFor.textContent);
                    }
                    const wrapping = el.closest("label");
                    if (wrapping) return normalize(wrapping.textContent);
                    return normalize(el.getAttribute("aria-label"));
                };
                const findHelper = (el) => {
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
                const findError = (el) => {
                    const fieldContainer = el.closest("[class*='field'], .application-field, .field");
                    if (!fieldContainer) return "";
                    const errorNode = fieldContainer.querySelector("[class*='error'], .error, [data-testid*='error']");
                    if (!errorNode) return "";
                    return normalize(errorNode.textContent);
                };
                const findHeading = (el) => {
                    const section = el.closest("section, fieldset, form, .section");
                    if (!section) return "";
                    const heading = section.querySelector("h1, h2, h3, h4, legend");
                    if (!heading) return "";
                    return normalize(heading.textContent);
                };
                const collectOptions = (el) => {
                    const options = [];
                    const tag = String(el.tagName || "").toLowerCase();
                    if (tag === "select") {
                        const nativeOptions = Array.from(el.options || []);
                        for (const option of nativeOptions) {
                            options.push({
                                label: normalize(option.label || option.textContent),
                                value: normalize(option.value),
                                selected: Boolean(option.selected),
                            });
                        }
                    } else {
                        const role = normalize(el.getAttribute("role"));
                        if (role === "combobox") {
                            const controls = normalize(el.getAttribute("aria-controls"));
                            if (controls) {
                                const listbox = document.getElementById(controls);
                                if (listbox) {
                                    const roleOptions = Array.from(listbox.querySelectorAll("[role='option']"));
                                    for (const option of roleOptions) {
                                        options.push({
                                            label: normalize(option.textContent),
                                            value: normalize(option.getAttribute("data-value") || option.textContent),
                                            selected: normalize(option.getAttribute("aria-selected")) === "true",
                                        });
                                    }
                                }
                            }
                        }
                    }
                    return options;
                };

                const selector = "input, textarea, select, [role='combobox'], [role='listbox'], [contenteditable='true']";
                const elements = Array.from(document.querySelectorAll(selector)).slice(0, Math.max(1, Number(limit) || 250));
                const records = [];
                for (const el of elements) {
                    const tagName = String(el.tagName || "").toLowerCase();
                    const inputType = normalize(el.getAttribute("type")).toLowerCase();
                    const role = normalize(el.getAttribute("role")).toLowerCase();
                    const value = tagName === "select" ? normalize(el.value) : normalize(el.value || el.textContent);
                    records.push({
                        dom_path: buildDomPath(el),
                        tag_name: tagName,
                        input_type: inputType,
                        role,
                        required: Boolean(el.required) || normalize(el.getAttribute("aria-required")) === "true",
                        visible: isVisible(el),
                        enabled: !Boolean(el.disabled),
                        label: findLabel(el),
                        helper_text: findHelper(el),
                        error_text: findError(el),
                        placeholder: normalize(el.getAttribute("placeholder")),
                        name: normalize(el.getAttribute("name")),
                        field_id: normalize(el.id),
                        value,
                        section_heading: findHeading(el),
                        options: collectOptions(el),
                    });
                }
                return records;
            }""",
            max_fields,
        )
    except Exception:
        return FormSnapshot(captured_at=utc_now().isoformat(), fields=())

    if not isinstance(rows, list):
        return FormSnapshot(captured_at=utc_now().isoformat(), fields=())
    fields: list[FormFieldSnapshot] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        options_raw = item.get("options")
        options: list[FormOptionSnapshot] = []
        if isinstance(options_raw, list):
            for option in options_raw:
                if not isinstance(option, dict):
                    continue
                options.append(
                    FormOptionSnapshot(
                        label=str(option.get("label", "")).strip(),
                        value=str(option.get("value", "")).strip(),
                        selected=bool(option.get("selected", False)),
                    )
                )
        fields.append(
            FormFieldSnapshot(
                dom_path=str(item.get("dom_path", "")).strip(),
                tag_name=str(item.get("tag_name", "")).strip(),
                input_type=str(item.get("input_type", "")).strip(),
                role=str(item.get("role", "")).strip(),
                required=bool(item.get("required", False)),
                visible=bool(item.get("visible", False)),
                enabled=bool(item.get("enabled", False)),
                label=str(item.get("label", "")).strip(),
                helper_text=str(item.get("helper_text", "")).strip(),
                error_text=str(item.get("error_text", "")).strip(),
                placeholder=str(item.get("placeholder", "")).strip(),
                name=str(item.get("name", "")).strip(),
                field_id=str(item.get("field_id", "")).strip(),
                value=str(item.get("value", "")).strip(),
                section_heading=str(item.get("section_heading", "")).strip(),
                options=tuple(options),
                intent=classify_field_intent(
                    label=str(item.get("label", "")).strip(),
                    name=str(item.get("name", "")).strip(),
                    field_id=str(item.get("field_id", "")).strip(),
                    helper_text=str(item.get("helper_text", "")).strip(),
                    tag_name=str(item.get("tag_name", "")).strip(),
                    input_type=str(item.get("input_type", "")).strip(),
                    role=str(item.get("role", "")).strip(),
                    options=[option.label for option in options],
                ),
                widget_type=classify_widget_type(
                    tag_name=str(item.get("tag_name", "")).strip(),
                    input_type=str(item.get("input_type", "")).strip(),
                    role=str(item.get("role", "")).strip(),
                ),
                constraint_mode=(
                    "option_only"
                    if is_option_constrained(
                        tag_name=str(item.get("tag_name", "")).strip(),
                        input_type=str(item.get("input_type", "")).strip(),
                        role=str(item.get("role", "")).strip(),
                    )
                    else "free_text"
                ),
                file_slot=classify_file_slot(
                    label=str(item.get("label", "")).strip(),
                    name=str(item.get("name", "")).strip(),
                    field_id=str(item.get("field_id", "")).strip(),
                    helper_text=str(item.get("helper_text", "")).strip(),
                ),
            )
        )
    return FormSnapshot(captured_at=utc_now().isoformat(), fields=tuple(fields))


def diagnostics_to_dict(diagnostics: FormFillDiagnostics) -> dict[str, Any]:
    return {
        "run_stage": diagnostics.run_stage,
        "detected_field_count": diagnostics.detected_field_count,
        "filled_count": diagnostics.filled_count,
        "unresolved_required": list(diagnostics.unresolved_required),
        "attempts": [
            {
                "semantic_key": attempt.semantic_key,
                "label": attempt.label,
                "adapter": attempt.adapter,
                "attempted_value": attempt.attempted_value,
                "success": attempt.success,
                "verified": attempt.verified,
                "reason": attempt.reason,
                "intent": attempt.intent,
                "widget_type": attempt.widget_type,
                "constraint_mode": attempt.constraint_mode,
                "failure_category": attempt.failure_category,
                "file_slot": attempt.file_slot,
            }
            for attempt in diagnostics.attempts
        ],
    }


__all__ = [
    "FormOptionSnapshot",
    "FormFieldSnapshot",
    "FormSnapshot",
    "FieldFillAttempt",
    "FormFillDiagnostics",
    "normalize_text",
    "normalize_key",
    "tokenize",
    "semantic_key_from_parts",
    "is_placeholder_value",
    "best_option_match",
    "classify_widget_type",
    "is_option_constrained",
    "classify_file_slot",
    "should_ignore_internal_field_candidate",
    "classify_field_intent",
    "group_fields_by_semantic_key",
    "capture_form_snapshot",
    "diagnostics_to_dict",
]
