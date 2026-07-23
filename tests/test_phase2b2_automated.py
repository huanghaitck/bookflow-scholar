from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from docx import Document

from bookflow.automated_reconstruction import (
    build_automated_boundaries,
    build_automated_logical_blocks,
    export_from_master,
    run_automated_reconstruction,
)
from bookflow.io_utils import load_json, sha256_file
from bookflow.paths import load_settings, project_root
from bookflow.phase2b2_schemas import (
    AutomatedBoundary,
    AutomatedLogicalBlock,
    AutomatedPageRecord,
    SourceFragment,
)
from bookflow.phase2b_schemas import BoundaryDecision


ROOT = project_root()
SAMPLE = ROOT / "input/sample_11_pages.pdf"
FULL = ROOT / "input/The big game of central and western China (1913).pdf"


def _output_settings(tmp_path: Path):
    settings = load_settings(ROOT / "config/settings.example.yaml")
    return settings.model_copy(
        update={
            "automated_page_directory": str(tmp_path / "pages"),
            "automated_boundary_directory": str(tmp_path / "boundaries"),
            "automated_logical_directory": str(tmp_path / "logical"),
            "automated_context_directory": str(tmp_path / "contexts"),
            "automated_audit_directory": str(tmp_path / "audits"),
            "automated_master_path": str(tmp_path / "bilingual_document.json"),
            "automated_export_directory": str(tmp_path / "diagnostic"),
        }
    )


@pytest.fixture(scope="module")
def automated_sample(tmp_path_factory):
    temp = tmp_path_factory.mktemp("phase2b2")
    settings = _output_settings(temp)
    raw_hashes = {
        path: sha256_file(path)
        for path in (ROOT / "data/vision_raw/zhipu/glm-4.6v").rglob("*.json")
    }
    result = run_automated_reconstruction(SAMPLE, settings, root=ROOT)
    assert all(sha256_file(path) == digest for path, digest in raw_hashes.items())
    pages = [
        AutomatedPageRecord.model_validate_json(line)
        for line in Path(result.page_records_path).read_text(encoding="utf-8").splitlines()
    ]
    boundaries = [
        AutomatedBoundary.model_validate_json(line)
        for line in Path(result.boundary_records_path).read_text(encoding="utf-8").splitlines()
    ]
    logical = [
        AutomatedLogicalBlock.model_validate_json(line)
        for line in Path(result.logical_blocks_path).read_text(encoding="utf-8").splitlines()
    ]
    return temp, settings, result, pages, boundaries, logical


def test_page_tail_fragment_is_preserved(automated_sample):
    _, _, _, pages, _, _ = automated_sample
    page5 = pages[4]
    assert page5.tail_fragment is not None
    assert page5.tail_fragment.text.endswith("before the")
    assert page5.tail_fragment.text in page5.full_visible_text


def test_page_head_fragment_is_not_marked_complete(automated_sample):
    _, _, _, pages, _, _ = automated_sample
    page6 = pages[5]
    assert page6.head_fragment is not None
    assert page6.head_fragment.text.startswith("boat sailed")
    assert page6.head_fragment.starts_mid_sentence
    assert page6.head_fragment.fragment_id not in page6.complete_blocks


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("the", "boat", "the boat"),
        ("much", "of", "much of"),
        ("range", "of", "range of"),
    ],
)
def test_complete_visible_words_keep_one_space(automated_sample, left, right, expected):
    _, _, _, _, _, logical = automated_sample
    matches = [block.source_text for block in logical if left in block.source_text and right in block.source_text]
    assert any(expected in text for text in matches)
    assert all((left + right) not in text for text in matches)


def test_chapter_break_never_connects(automated_sample):
    _, _, _, _, boundaries, logical = automated_sample
    boundary = next(item for item in boundaries if item.previous_page == 8)
    assert boundary.structural_break == "chapter_break"
    assert boundary.join_operation == "no_join"
    assert not any(block.source_pages == [8, 9] for block in logical)


def test_actual_cross_page_paragraphs_are_reconstructed(automated_sample):
    _, _, _, _, _, logical = automated_sample
    page_ranges = {tuple(block.source_pages) for block in logical if block.cross_page}
    assert {(1, 2), (5, 6), (6, 7), (7, 8), (9, 10), (10, 11)} <= page_ranges


def test_sample_last_incomplete_paragraph_is_not_translation_ready(automated_sample):
    _, _, _, _, boundaries, logical = automated_sample
    open_boundary = next(item for item in boundaries if item.previous_page == 11)
    assert open_boundary.auto_resolution_status == "unresolved"
    final = [block for block in logical if 11 in block.source_pages and block.block_type == "body"][-1]
    assert final.sentence_complete is False
    assert final.paragraph_complete is False
    assert final.translation_ready is False
    assert open_boundary.boundary_id in final.unresolved_boundaries


def test_every_source_fragment_is_used_exactly_once(automated_sample):
    _, _, result, pages, _, logical = automated_sample
    expected = [fragment.fragment_id for page in pages for fragment in page.content_fragments]
    used = [fragment_id for block in logical for fragment_id in block.source_fragment_ids]
    assert sorted(expected) == sorted(used)
    assert len(used) == len(set(used))
    audit = load_json(result.logical_audit_path)
    assert audit["unreferenced_fragment_ids"] == []
    assert audit["duplicate_fragment_ids"] == []
    assert audit["internal_unresolved_boundary_ids"] == []
    assert audit["external_open_boundary_ids"] == ["boundary_p0011_p0012"]
    assert audit["internal_boundaries_passed"] is True


def test_all_eleven_pages_pass_source_coverage_audit(automated_sample):
    _, _, result, pages, _, _ = automated_sample
    audit = load_json(result.source_audit_path)
    assert len(pages) == audit["processed_pages"] == audit["total_pages"] == 11
    assert audit["missing_pages"] == []
    assert audit["duplicate_pages"] == []
    assert audit["partial_coverage_pages"] == []
    assert audit["passed"] is True


def test_strict_export_is_blocked_by_unresolved_and_missing_translation(automated_sample, tmp_path):
    _, _, result, _, _, _ = automated_sample
    export = export_from_master(result.master_document_path, tmp_path / "strict", mode="strict")
    assert export.blocked
    assert export.markdown_path is None and export.word_path is None
    assert not list((tmp_path / "strict").glob("*final*"))


def test_permissive_export_has_clear_markers_and_is_not_final(automated_sample):
    _, _, result, _, _, _ = automated_sample
    markdown = Path(result.diagnostic_markdown_path).read_text(encoding="utf-8")
    assert "[UNRESOLVED_BOUNDARY]" in markdown
    assert "[INCOMPLETE_SOURCE]" in markdown
    assert "非final" in markdown
    assert "final" not in Path(result.diagnostic_markdown_path).stem
    assert "final" not in Path(result.diagnostic_word_path).stem


def test_word_and_markdown_use_same_master_source(automated_sample):
    _, _, result, _, _, logical = automated_sample
    markdown = Path(result.diagnostic_markdown_path).read_text(encoding="utf-8")
    word_text = "\n".join(paragraph.text for paragraph in Document(result.diagnostic_word_path).paragraphs)
    for block in logical:
        assert block.source_text in markdown
        assert block.source_text in word_text


def test_new_schema_has_no_human_review_dependency(automated_sample):
    _, _, _, pages, boundaries, logical = automated_sample
    combined = json.dumps(
        {
            "pages": [item.model_dump(mode="json") for item in pages],
            "boundaries": [item.model_dump(mode="json") for item in boundaries],
            "logical": [item.model_dump(mode="json") for item in logical],
        }
    )
    assert "human_review" not in combined
    assert "suggested_human_action" not in combined


def test_phase2b2_is_offline_and_zero_calls(automated_sample, monkeypatch):
    _, _, result, _, boundaries, _ = automated_sample
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    assert result.api_calls == result.deepseek_calls == result.translation_calls == 0
    assert all(not item.text_adjudicator_called and not item.translation_called for item in boundaries)


def test_full_pdf_is_rejected_before_body_processing(tmp_path):
    with pytest.raises(PermissionError, match="only accepts"):
        run_automated_reconstruction(FULL, _output_settings(tmp_path), root=ROOT)


def _synthetic_page(page: int, fragment: SourceFragment) -> AutomatedPageRecord:
    return AutomatedPageRecord(
        schema_version="2.0",
        document_id="doc",
        pdf_page=page,
        printed_page=str(page),
        page_type="printed",
        full_visible_text=fragment.text,
        complete_blocks=[],
        head_fragment=fragment if fragment.starts_mid_paragraph else None,
        tail_fragment=fragment if fragment.ends_mid_paragraph else None,
        content_fragments=[fragment],
        running_header=None,
        footer=None,
        page_number_text=str(page),
        titles=[],
        image_sha256=str(page) * 64,
        text_layer_text=fragment.text,
        text_layer_similarity=1.0,
        transcription_status="technical_validation_only",
        source_coverage_status="complete",
        legacy_normalized_path=f"page{page}.json",
        created_at=datetime.now(timezone.utc),
    )


def test_paragraph_can_span_three_pages(tmp_path):
    texts = ["A paragraph continues", "through its middle", "and ends here."]
    fragments = [
        SourceFragment(
            fragment_id=f"f{page}", text=text, source_page=page,
            source_block_ids=[f"p{page}:b"], block_type="body", order=1,
            starts_mid_sentence=page > 1, ends_mid_sentence=page < 3,
            starts_mid_paragraph=page > 1, ends_mid_paragraph=page < 3,
            visible_trailing_hyphen=False, uncertainty=[],
        )
        for page, text in enumerate(texts, 1)
    ]
    pages = [_synthetic_page(page, fragment) for page, fragment in enumerate(fragments, 1)]
    boundaries = [
        AutomatedBoundary(
            schema_version="2.0", boundary_id=f"boundary_p{page:04d}_p{page+1:04d}",
            document_id="doc", previous_page=page, next_page=page + 1,
            previous_fragment_id=f"f{page}", next_fragment_id=f"f{page+1}",
            previous_tail_text=texts[page - 1], next_head_text=texts[page],
            word_continuation=False, sentence_continuation=True, paragraph_continuation=True,
            structural_break="none", join_operation="insert_space", visible_trailing_hyphen=False,
            resolution_method="test", supporting_evidence=["test"], conflicting_evidence=[],
            resolution_reason="test", auto_resolution_status="resolved_primary", source_inputs=[],
            created_at=datetime.now(timezone.utc),
        )
        for page in (1, 2)
    ]
    settings = _output_settings(tmp_path)
    logical, _ = build_automated_logical_blocks(pages, boundaries, settings, root=ROOT)
    assert len(logical) == 1
    assert logical[0].source_pages == [1, 2, 3]
    assert logical[0].source_text == "A paragraph continues through its middle and ends here."
    assert logical[0].translation_ready


def test_boundary_builder_uses_dynamic_page_count_without_open_boundary(tmp_path):
    texts = ["It continues", "through here.", "A new paragraph."]
    fragments = [
        SourceFragment(
            fragment_id=f"d{page}", text=text, source_page=page,
            source_block_ids=[f"p{page}:b"], block_type="body", order=1,
            starts_mid_sentence=page == 2, ends_mid_sentence=page == 1,
            starts_mid_paragraph=page == 2, ends_mid_paragraph=page == 1,
            visible_trailing_hyphen=False, uncertainty=[],
        )
        for page, text in enumerate(texts, 1)
    ]
    pages = [_synthetic_page(page, fragment) for page, fragment in enumerate(fragments, 1)]

    boundaries = build_automated_boundaries(
        pages, _output_settings(tmp_path), root=ROOT,
        include_open_boundary=False, legacy_evidence={}, artifact_stem="fullbook",
    )

    assert [(item.previous_page, item.next_page) for item in boundaries] == [(1, 2), (2, 3)]
    assert not any(item.next_page == 4 for item in boundaries)


def test_pair_evidence_resolves_uppercase_continuation_when_primary_is_uncertain(tmp_path):
    fragments = [
        SourceFragment(
            fragment_id="left", text="They were doing", source_page=1,
            source_block_ids=["p1:b"], block_type="body", order=1,
            starts_mid_sentence=False, ends_mid_sentence=True,
            starts_mid_paragraph=False, ends_mid_paragraph=True,
            visible_trailing_hyphen=False, uncertainty=[],
        ),
        SourceFragment(
            fragment_id="right", text="King Edward's work.", source_page=2,
            source_block_ids=["p2:b"], block_type="body", order=1,
            starts_mid_sentence=True, ends_mid_sentence=False,
            starts_mid_paragraph=True, ends_mid_paragraph=False,
            visible_trailing_hyphen=False, uncertainty=[],
        ),
    ]
    pages = [_synthetic_page(index, fragment) for index, fragment in enumerate(fragments, 1)]
    pair = BoundaryDecision(
        schema_version="1.0", boundary_id="boundary_p0001_p0002", document_id="doc",
        previous_page=1, next_page=2, previous_image_sha256="1" * 64,
        next_image_sha256="2" * 64, previous_last_block_id="b", next_first_block_id="b",
        previous_tail_text=fragments[0].text, next_head_text=fragments[1].text,
        word_continuation=False, sentence_continuation=True, paragraph_continuation=True,
        structural_break="none", join_operation="concatenate_with_space",
        hyphen_type="no_hyphen", header_footer_interference=False,
        reconstructed_boundary_text="doing King", evidence=["visible pair"], confidence=0.9,
        provider="test", model="test", review_window=[1, 2], raw_response_path=None,
        normalization_events=[], model_review_status="completed",
        human_review_status="not_required", needs_triple_review=False, status="reviewed",
    )

    boundaries = build_automated_boundaries(
        pages, _output_settings(tmp_path), root=ROOT, include_open_boundary=False,
        legacy_evidence={pair.boundary_id: pair}, artifact_stem="fullbook",
    )

    assert boundaries[0].auto_resolution_status == "resolved_pair"
    assert boundaries[0].join_operation == "insert_space"
    assert boundaries[0].sentence_continuation is True
    assert boundaries[0].paragraph_continuation is True
