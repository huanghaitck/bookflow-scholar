from __future__ import annotations

import json
import shutil
from pathlib import Path

import fitz
import pytest

from bookflow.paths import project_root
from bookflow.phase5_audit import (
    Phase5Gate,
    audit_candidate_outputs,
    audit_pdf_files,
    audit_source_and_alignment,
    evaluate_release_gate,
    normalize_rendered_text,
    normalize_visible_text,
    publish_phase5_release,
)


ROOT = project_root()


@pytest.fixture(scope="module")
def data_audit():
    return audit_source_and_alignment(root=ROOT)


@pytest.fixture(scope="module")
def output_audit():
    return audit_candidate_outputs(root=ROOT)


@pytest.fixture(scope="module")
def render_audit(tmp_path_factory):
    output = tmp_path_factory.mktemp("phase5-render")
    return audit_pdf_files(
        source_pdf=ROOT / "output/rendered/source_english_sample12.pdf",
        bilingual_pdf=ROOT / "output/rendered/bilingual_zh-Hans_sample12.pdf",
        source_docx=ROOT / "output/candidate/source_english_sample12.docx",
        bilingual_docx=ROOT / "output/candidate/bilingual_zh-Hans_sample12.docx",
        output_directory=output,
    )


def test_source_and_bilingual_block_ids_match(data_audit) -> None:
    assert data_audit.block_ids_match is True


def test_source_text_matches_block_by_block(data_audit) -> None:
    assert data_audit.source_text_match is True


def test_source_fragment_total_is_33(data_audit) -> None:
    assert data_audit.source_fragment_count == 33


def test_no_source_fragment_is_unused(data_audit) -> None:
    assert data_audit.unused_fragment_ids == []


def test_no_source_fragment_is_reused(data_audit) -> None:
    assert data_audit.duplicate_fragment_ids == []


def test_no_unresolved_boundary_remains(data_audit) -> None:
    assert data_audit.unresolved_boundary_ids == []


def test_doing_king_is_present(data_audit) -> None:
    assert data_audit.doing_king_present is True


def test_doingking_is_absent(data_audit) -> None:
    assert data_audit.doingking_absent is True


def test_translation_count_is_24(data_audit) -> None:
    assert data_audit.translation_count == 24


def test_missing_translation_count_is_zero(data_audit) -> None:
    assert data_audit.missing_translation_ids == []


def test_duplicate_translation_count_is_zero(data_audit) -> None:
    assert data_audit.duplicate_translation_ids == []


def test_extra_translation_count_is_zero(data_audit) -> None:
    assert data_audit.extra_translation_ids == []


def test_context_leakage_detection_is_clear_for_actual_data(data_audit) -> None:
    assert data_audit.context_leakage_ids == []


def test_source_markdown_matches_json(output_audit) -> None:
    assert output_audit.source_markdown_matches_json is True


def test_source_docx_matches_json(output_audit) -> None:
    assert output_audit.source_docx_matches_json is True


def test_bilingual_markdown_matches_json(output_audit) -> None:
    assert output_audit.bilingual_markdown_matches_json is True


def test_bilingual_docx_matches_json(output_audit) -> None:
    assert output_audit.bilingual_docx_matches_json is True


def test_source_markdown_and_docx_match(output_audit) -> None:
    assert output_audit.source_markdown_matches_docx is True


def test_bilingual_markdown_and_docx_match(output_audit) -> None:
    assert output_audit.bilingual_markdown_matches_docx is True


def test_pdf_page_counts_are_positive(render_audit) -> None:
    assert render_audit.source_page_count > 0
    assert render_audit.bilingual_page_count > 0


def test_rendered_pdf_has_no_blank_pages(render_audit) -> None:
    assert render_audit.blank_source_pages == []
    assert render_audit.blank_bilingual_pages == []


def test_pdf_text_matches_docx(render_audit) -> None:
    assert render_audit.source_pdf_matches_docx is True
    assert render_audit.bilingual_pdf_matches_docx is True


def test_all_pdf_pages_render_to_png(render_audit) -> None:
    assert len(render_audit.source_page_images) == render_audit.source_page_count
    assert len(render_audit.bilingual_page_images) == render_audit.bilingual_page_count
    assert all(Path(path).is_file() for path in render_audit.source_page_images)
    assert all(Path(path).is_file() for path in render_audit.bilingual_page_images)


def test_contact_sheets_are_created(render_audit) -> None:
    assert Path(render_audit.source_contact_sheet_path).is_file()
    assert Path(render_audit.bilingual_contact_sheet_path).is_file()


def test_release_gate_requires_every_audit(data_audit, output_audit, render_audit) -> None:
    gate = evaluate_release_gate(
        data_audit=data_audit,
        output_audit=output_audit,
        render_audit=render_audit,
        tests_passed=False,
        replay_api_calls=0,
    )
    assert gate.passed is False
    assert "automatic tests did not pass" in gate.blockers


def test_failed_gate_does_not_publish_bilingual_final(tmp_path: Path) -> None:
    gate = Phase5Gate(passed=False, blockers=["synthetic failure"])
    result = publish_phase5_release(
        root=tmp_path,
        gate=gate,
        test_count=208,
        human_visual_observation="效果很好",
    )
    assert result.published is False
    assert not (tmp_path / "output/final/bilingual_zh-Hans.docx").exists()


def test_release_manifest_contains_final_hashes(tmp_path: Path, data_audit, output_audit, render_audit) -> None:
    for relative in (
        "output/candidate/source_english_sample12.md",
        "output/candidate/source_english_sample12.docx",
        "output/candidate/bilingual_zh-Hans_sample12.md",
        "output/candidate/bilingual_zh-Hans_sample12.docx",
        "output/audit/phase5/converted/source_english_sample12.pdf",
        "output/audit/phase5/converted/bilingual_zh-Hans_sample12.pdf",
        "data/source_document_sample12_v1.json",
        "data/bilingual_document_sample12_zh-Hans_v1.json",
    ):
        source = ROOT / relative
        if "output/audit/phase5/converted" in relative:
            source = ROOT / "output/rendered" / Path(relative).name
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    gate = Phase5Gate(passed=True, blockers=[])
    publishable_output_audit = output_audit.model_copy(update={
        "existing_source_final_markdown_matches_candidate": True,
        "existing_source_final_docx_matches_candidate": True,
        "strict_passed": True,
        "blockers": [],
    })
    result = publish_phase5_release(
        root=tmp_path,
        gate=gate,
        test_count=208,
        human_visual_observation="效果很好",
        data_audit=data_audit,
        output_audit=publishable_output_audit,
        render_audit=render_audit,
    )
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert result.published is True
    assert len(manifest["final_files"]) == 4
    assert all(len(item["sha256"]) == 64 for item in manifest["final_files"])


def test_second_audit_run_has_zero_api_calls(data_audit) -> None:
    assert data_audit.glm_calls == 0
    assert data_audit.deepseek_calls == 0
    assert data_audit.translation_calls == 0


def test_phase5_does_not_call_glm(data_audit) -> None:
    assert data_audit.glm_calls == 0


def test_phase5_does_not_call_deepseek(data_audit) -> None:
    assert data_audit.deepseek_calls == 0


def test_normalization_ignores_only_markdown_style_and_whitespace() -> None:
    assert normalize_visible_text("# TITLE\n\nA   sentence.", markdown=True) == "TITLE A sentence."
    assert normalize_visible_text("A sentence!", markdown=False) != normalize_visible_text(
        "A sentence.", markdown=False
    )


def test_rendered_normalization_ignores_pagination_whitespace_only() -> None:
    assert normalize_rendered_text("car- conductor\n中文 换行") == normalize_rendered_text(
        "car-conductor中文换行"
    )
    assert normalize_rendered_text("doing King") != normalize_rendered_text("doingKing!")


def test_blank_pdf_page_is_detected(tmp_path: Path) -> None:
    blank = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(blank)
    doc.close()
    result = audit_pdf_files(
        source_pdf=blank,
        bilingual_pdf=blank,
        source_docx=None,
        bilingual_docx=None,
        output_directory=tmp_path / "audit",
    )
    assert result.blank_source_pages == [1]
    assert result.blank_bilingual_pages == [1]


def test_existing_final_comparison_is_recorded(output_audit) -> None:
    assert isinstance(output_audit.existing_source_final_markdown_matches_candidate, bool)
    assert isinstance(output_audit.existing_source_final_docx_matches_candidate, bool)
