from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from docx import Document

from bookflow.appendix_recovery import build_appendix_reading_order, validate_appendix_model
from bookflow.production import (
    RenderInputs,
    TranslationResolver,
    calculate_release_eligibility,
    render_docx,
    render_markdown,
)
import bookflow.production as production
from bookflow.translation_runner import TranslationRunner


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def model():
    return build_appendix_reading_order(ROOT)


def test_row_group_belongs_to_appendix_through_table_id(model):
    appendix_a = next(item for item in model["appendices"] if item["appendix_id"] == "appendix_a")
    row = next(item for item in appendix_a["elements"] if item["element_type"] == "table_row")
    tables = [json.loads(line) for line in (ROOT / "data/fullbook/back_matter/tables.jsonl").read_text("utf-8").splitlines() if line]
    table = next(item for item in tables if item["table_id"] == row["table_id"])
    assert table["appendix_id"] == "appendix_a"


def test_raw_ordered_text_is_preserved(model):
    row = next(element for appendix in model["appendices"] for element in appendix["elements"] if element["element_type"] == "table_row")
    assert isinstance(row["raw_ordered_text"], list)
    assert row["source_text"] == " | ".join(row["raw_ordered_text"])


def test_existing_overlay_translation_is_resolved():
    inputs = RenderInputs.load(ROOT)
    resolver = TranslationResolver(inputs)
    appendix_title = next(unit for unit in inputs.units if unit["source_object_type"] == "appendix_title" and unit["translation_status"] == "validated")
    translated, status = resolver.resolve(appendix_title["source_object_id"], appendix_title["source_text"], "zh-Hans", "preview")
    assert status == "validated"
    assert translated and translated != appendix_title["source_text"]


def test_all_appendix_source_pages_are_recovered(model):
    validation = model["validation"]
    assert validation["source_page_coverage"]["appendix_a"] == list(range(381, 398))
    assert 399 in validation["source_page_coverage"]["appendix_b"]
    assert validation["source_page_coverage"]["appendix_c"] == list(range(400, 405))
    assert validation["table_row_count"] == 301


def test_prose_recovery_units_are_validated_after_production():
    inputs = RenderInputs.load(ROOT)
    recovered = [unit for unit in inputs.units if unit["source_object_type"] == "appendix_element"]
    assert recovered
    assert {page for unit in recovered for page in unit["physical_pages"]} == {*range(388, 398), 399}
    assert {unit["translation_status"] for unit in recovered} == {"validated"}
    assert len(recovered) == 132


def test_heading_only_appendix_fails_validation(model):
    broken = copy.deepcopy(model)
    broken["appendices"][0]["elements"] = broken["appendices"][0]["elements"][:1]
    validation = validate_appendix_model(broken)
    assert not validation["valid"]
    assert "appendix_a" in validation["heading_only_appendices"]


def test_facsimile_fallback_is_present_for_unconfirmed_columns(model):
    facsimiles = [element for appendix in model["appendices"] for element in appendix["elements"] if element["element_type"] == "facsimile"]
    assert len(facsimiles) == 13
    assert all((ROOT / element["source_page_asset_ref"]).is_file() for element in facsimiles)


def test_completed_appendix_units_allow_non_english_release():
    eligibility = calculate_release_eligibility(ROOT)
    assert eligibility["pending_translatable"] == 0
    assert eligibility["blockers"] == []
    assert eligibility["eligible"] is True
    status = TranslationRunner(ROOT).status()
    assert status["production_checkpoint_status"] == "completed"
    assert status["next_action"] == "build_first_book_releases"


def test_markdown_and_docx_render_the_same_appendix_elements(tmp_path, model):
    inputs = RenderInputs.load(ROOT)
    markdown = render_markdown(inputs, tmp_path / "markdown", "en", "preview")
    docx = render_docx(inputs, tmp_path / "docx", "en", "preview")
    expected = model["validation"]["appendix_element_count"]
    assert markdown["appendix_element_count"] == docx["appendix_element_count"] == expected
    assert markdown["appendix_source_page_count"] == docx["appendix_source_page_count"] == 24
    appendix_b = next(item for item in model["appendices"] if item["appendix_id"] == "appendix_b")
    page_399 = next(item["source_text"] for item in appendix_b["elements"] if item.get("physical_page") == 399)
    assert page_399 in Path(markdown["path"]).read_text("utf-8")
    rendered_text = "\n".join(paragraph.text for paragraph in Document(docx["path"]).paragraphs)
    assert page_399 in rendered_text


def test_pdf_adapter_carries_the_same_appendix_count(tmp_path, monkeypatch, model):
    def fake_pdf(docx_path, out, language, pairs, timeout=600):
        out.mkdir(parents=True, exist_ok=True)
        path = out / "fixture.pdf"
        path.write_bytes(b"%PDF-1.4\n% appendix-count fixture\n")
        return {"status": "completed", "path": str(path), "sha256": "fixture", "pages": 1, "size": path.stat().st_size, "pagination": {"valid": True}}

    monkeypatch.setattr(production, "convert_pdf", fake_pdf)
    monkeypatch.setattr(production, "generate_visual_qa", lambda *_: {"contact_sheets": [], "high_resolution": []})
    result = production.build(ROOT, "en", "preview", ("md", "docx", "pdf"), output_root=tmp_path)
    counts = {name: output["appendix_element_count"] for name, output in result["outputs"].items()}
    assert counts == {"markdown": 462, "docx": 462, "pdf": 462}
    assert len(set(counts.values())) == 1
