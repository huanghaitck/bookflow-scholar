from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bookflow.main_text_edition import (
    MainTextScope,
    audit_main_text_terminal_state,
    deferred_page_records,
    verified_blank_state,
    write_scope_artifacts,
    effective_translatable_block_type,
    effective_body_page_pairs,
    validated_pair_resolution,
    boundary_leaves_fragment_unresolved,
)


SOURCE_HASH = "a" * 64


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_main_text_scope_is_explicitly_not_the_complete_book() -> None:
    scope = MainTextScope(
        source_pdf="input/book.pdf",
        source_pdf_sha256=SOURCE_HASH,
        full_pdf_actual_page_count=412,
    )

    assert scope.document_scope == "main_text_edition"
    assert scope.source_page_start == 1
    assert scope.source_page_end == 379
    assert scope.deferred_page_start == 380
    assert scope.deferred_page_end == 412
    assert scope.is_complete_main_text_edition is True
    assert scope.is_complete_full_book is False
    assert scope.actual_page_count == 379


def test_back_matter_pages_are_deferred_not_missing_or_failed() -> None:
    scope = MainTextScope(
        source_pdf="input/book.pdf",
        source_pdf_sha256=SOURCE_HASH,
        full_pdf_actual_page_count=412,
    )

    records = deferred_page_records(scope)

    assert len(records) == 33
    assert records[0]["pdf_page"] == 380
    assert records[-1]["pdf_page"] == 412
    assert {item["processing_status"] for item in records} == {"deferred"}
    assert {item["deferred_reason"] for item in records} == {
        "complex_back_matter_deferred_for_later_release"
    }


def test_scope_artifacts_are_isolated_under_main_text(tmp_path: Path) -> None:
    scope = MainTextScope(
        source_pdf="input/book.pdf",
        source_pdf_sha256=SOURCE_HASH,
        full_pdf_actual_page_count=412,
    )

    scope_path, deferred_path = write_scope_artifacts(tmp_path, scope)

    assert scope_path == tmp_path / "data/fullbook/main_text/scope.json"
    assert deferred_path == tmp_path / "data/fullbook/main_text/deferred_pages.json"
    assert json.loads(scope_path.read_text(encoding="utf-8"))["is_complete_full_book"] is False
    assert len(json.loads(deferred_path.read_text(encoding="utf-8"))) == 33


def test_terminal_audit_accepts_human_blank_and_ignores_back_matter_quarantine(
    tmp_path: Path,
) -> None:
    vision = tmp_path / "data/fullbook/vision"
    for page in range(1, 380):
        status = "final_blank" if page == 110 else "completed"
        _write_json(vision / "cache" / f"page_{page:04d}.json", {"pdf_page": page, "status": status})
    _write_json(
        vision / "final_states/page_0110.json",
        {
            "pdf_page": 110,
            "page_type": "blank",
            "transcription_status": "not_required_blank",
            "human_verified": True,
            "quarantine": False,
            "source_fragments": [],
        },
    )
    _write_json(
        tmp_path / "data/fullbook/checkpoints/production.json",
        {
            "source_pdf_sha256": SOURCE_HASH,
            "quarantine": {"vision_single": {"page_0384": {"error": "deferred"}}},
        },
    )
    _write_json(
        vision / "call_ledger.json",
        {"source_pdf_sha256": SOURCE_HASH, "attempts": []},
    )
    scope = MainTextScope(
        source_pdf="input/book.pdf",
        source_pdf_sha256=SOURCE_HASH,
        full_pdf_actual_page_count=412,
    )

    result = audit_main_text_terminal_state(tmp_path, scope)

    assert result.passed is True
    assert result.terminal_pages == 379
    assert result.blank_pages == [110]
    assert result.missing_pages == []
    assert result.unresolved_pages == []
    assert result.in_flight_pages == []
    assert result.out_of_scope_quarantine_pages == [384]


def test_verified_blank_state_has_no_source_fragments(tmp_path: Path) -> None:
    path = tmp_path / "data/fullbook/vision/final_states/page_0110.json"
    _write_json(
        path,
        {
            "pdf_page": 110,
            "page_type": "blank",
            "transcription_status": "not_required_blank",
            "human_verified": True,
            "quarantine": False,
            "source_fragments": [],
            "text_blocks": [],
        },
    )

    state = verified_blank_state(tmp_path, 110)

    assert state is not None
    assert state["page_type"] == "blank"
    assert state["source_fragments"] == []
    assert state["transcription_status"] == "not_required_blank"


def test_semantic_illustration_text_is_preserved_as_caption() -> None:
    assert effective_translatable_block_type("illustration", None, "A VIEW ON THE YANGTSE-KIANG.") == "caption"
    assert (
        effective_translatable_block_type(
            "body", None, "The existence of a sheep growing such horns...", "illustrated_page"
        )
        == "caption"
    )


def test_headers_watermarks_and_empty_illustrations_are_excluded() -> None:
    assert effective_translatable_block_type("header", None, "CHAPTER I") is None
    assert effective_translatable_block_type("unknown", "watermark", "Digitized by Microsoft") is None
    assert effective_translatable_block_type("illustration", None, "  ") is None


def test_effective_body_adjacency_can_cross_nontext_pages() -> None:
    def page(number: int, *types: str) -> SimpleNamespace:
        fragments = [SimpleNamespace(block_type=kind) for kind in types]
        return SimpleNamespace(pdf_page=number, content_fragments=fragments)

    pages = [
        page(109, "body"),
        page(110),
        page(111, "body"),
        page(196, "body"),
        page(197, "caption"),
        page(198, "other_translatable"),
        page(199, "body"),
        page(379, "body"),
    ]

    pairs = effective_body_page_pairs(pages)

    assert (109, 111) in pairs
    assert (196, 199) in pairs
    assert pairs[-1][1] == 379
    assert all(right <= 379 for _, right in pairs)


def test_pair_word_observation_cannot_remove_a_visible_word_boundary() -> None:
    result = validated_pair_resolution(
        model_status="completed",
        structural_break="none",
        join_operation="concatenate_with_space",
        hyphen_type="no_hyphen",
        word_continuation=True,
        sentence_continuation=True,
        paragraph_continuation=True,
        visible_trailing_hyphen=False,
    )

    assert result == {
        "auto_resolution_status": "resolved_pair",
        "structural_break": "none",
        "join_operation": "insert_space",
        "word_continuation": False,
        "sentence_continuation": True,
        "paragraph_continuation": True,
    }


def test_pair_section_break_is_a_safe_no_join_even_with_nullable_observations() -> None:
    result = validated_pair_resolution(
        model_status="needs_review",
        structural_break="section_break",
        join_operation="no_join",
        hyphen_type="no_hyphen",
        word_continuation=None,
        sentence_continuation=None,
        paragraph_continuation=None,
        visible_trailing_hyphen=False,
    )

    assert result is not None
    assert result["auto_resolution_status"] == "resolved_pair"
    assert result["join_operation"] == "no_join"
    assert result["paragraph_continuation"] is False


def test_no_visible_hyphen_forces_space_even_if_model_requests_zero_space() -> None:
    result = validated_pair_resolution(
        model_status="completed", structural_break="none",
        join_operation="concatenate_without_space", hyphen_type="no_hyphen",
        word_continuation=True, sentence_continuation=True,
        paragraph_continuation=True, visible_trailing_hyphen=False,
        left_token="the", right_token="Manchu",
    )
    assert result is not None
    assert result["join_operation"] == "insert_space"
    assert result["word_continuation"] is False


def test_visible_cross_page_hyphen_can_be_removed_when_both_parts_form_one_word() -> None:
    result = validated_pair_resolution(
        model_status="completed", structural_break="none",
        join_operation="concatenate_without_space", hyphen_type="no_hyphen",
        word_continuation=True, sentence_continuation=True,
        paragraph_continuation=True, visible_trailing_hyphen=True,
        left_token="en", right_token="deavoured",
    )
    assert result is not None
    assert result["join_operation"] == "remove_layout_hyphen"
    assert result["word_continuation"] is True


def test_incomplete_function_word_overrides_false_paragraph_observation() -> None:
    result = validated_pair_resolution(
        model_status="completed", structural_break="paragraph_break",
        join_operation="concatenate_without_space", hyphen_type="no_hyphen",
        word_continuation=True, sentence_continuation=True,
        paragraph_continuation=False, visible_trailing_hyphen=False,
        left_token="the", right_token="tribe",
    )
    assert result is not None
    assert result["structural_break"] == "none"
    assert result["paragraph_continuation"] is True
    assert result["join_operation"] == "insert_space"


def test_resolved_no_join_closes_stale_single_page_fragment_flags() -> None:
    boundary = SimpleNamespace(
        auto_resolution_status="resolved_pair",
        paragraph_continuation=False,
        previous_fragment_id="left",
        next_fragment_id="right",
    )
    assert boundary_leaves_fragment_unresolved(boundary, {"left"}, incoming=False) is False
    assert boundary_leaves_fragment_unresolved(boundary, {"right"}, incoming=True) is False
