from __future__ import annotations

import asyncio

from cloud_automation.services.form_engine import (
    best_option_match,
    capture_form_snapshot,
    classify_field_intent,
    classify_file_slot,
    classify_widget_type,
    is_option_constrained,
    is_placeholder_value,
    semantic_key_from_parts,
)


class _NoEvalPage:
    pass


def test_best_option_match_resolves_yes_no_aliases() -> None:
    selected = best_option_match(
        "authorized to work",
        ["Select...", "Yes", "No"],
    )
    assert selected == "Yes"


def test_best_option_match_uses_token_overlap() -> None:
    selected = best_option_match(
        "Austin Texas",
        ["Seattle, Washington", "Austin, Texas, United States"],
    )
    assert selected == "Austin, Texas, United States"


def test_capture_form_snapshot_without_evaluate_returns_empty_snapshot() -> None:
    snapshot = asyncio.run(capture_form_snapshot(_NoEvalPage()))
    assert snapshot.fields == ()
    assert isinstance(snapshot.captured_at, str)


def test_semantic_key_from_parts_is_stable() -> None:
    key = semantic_key_from_parts(
        label="Are you authorized to work in the country for which you applied?",
        name="question_authorized",
        field_id="question_123",
        helper_text="",
        option_labels=["Yes", "No"],
    )
    assert "authorized" in key


def test_placeholder_value_detection() -> None:
    assert is_placeholder_value("Select...")
    assert not is_placeholder_value("Yes")


def test_classify_widget_and_constraint_for_select() -> None:
    widget = classify_widget_type(tag_name="select", input_type="", role="")
    constrained = is_option_constrained(tag_name="select", input_type="", role="")
    assert widget == "native_select"
    assert constrained is True


def test_classify_file_slot_resume_and_cover_letter() -> None:
    resume_slot = classify_file_slot(
        label="Resume/CV",
        name="resume",
        field_id="resume",
        helper_text="",
    )
    cover_slot = classify_file_slot(
        label="Cover Letter",
        name="cover_letter",
        field_id="cover_letter",
        helper_text="Upload cover letter",
    )
    assert resume_slot == "resume"
    assert cover_slot == "cover_letter"


def test_classify_field_intent_short_vs_long_form() -> None:
    short_intent = classify_field_intent(
        label="Please provide the name of your current company",
        name="question_current_company",
        field_id="question_100",
        helper_text="",
        tag_name="input",
        input_type="text",
        role="",
        options=[],
    )
    long_intent = classify_field_intent(
        label="Why do you want to join?",
        name="question_why",
        field_id="question_101",
        helper_text="Please share 3-4 sentences",
        tag_name="textarea",
        input_type="",
        role="",
        options=[],
    )
    assert short_intent == "short_fact_text"
    assert long_intent == "long_form_text"
