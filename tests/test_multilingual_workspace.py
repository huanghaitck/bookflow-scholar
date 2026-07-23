from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path

import fitz
import pytest

from bookflow.manual_review import export_review_package
from bookflow.multilingual_workspace import (
    OUTPUT_ROLES,
    SUPPORTED_LANGUAGES,
    build_workspace,
    create_workspace,
    detect_language,
    inspect_workspace,
    plan_workspace,
    status_workspace,
    translate_workspace,
    validate_language,
)
from bookflow.providers.mock import MockTranslationProvider


SAMPLES = {
    "zh-Hans": "这是一本新的书籍，包含中文标点。",
    "en": "This is a new book, with English punctuation.",
    "fr": "C’est un livre français : « déjà vu », où l’été finit.",
    "de": "Dies ist ein deutsches Buch: Größen, äußere Wörter.",
    "ja": "これは新しい本です。日本語の文章を保存します。",
    "es": "¿Qué libro es éste? ¡Una edición española, señor!",
}


def _pdf(path: Path, text: str, language: str) -> None:
    doc = fitz.open(); page = doc.new_page()
    font = "japan" if language == "ja" else "china-s" if language == "zh-Hans" else "helv"
    page.insert_text((50, 70), text, fontname=font, fontsize=11)
    doc.save(path); doc.close()


def _lifecycle(tmp_path: Path, source: str, target: str, *, formats=("md",)) -> Path:
    pdf = tmp_path / f"source {source} ünicode.pdf"; _pdf(pdf, SAMPLES[source], source)
    workspace = tmp_path / f"workspace-{source}-{target}"
    create_workspace(workspace, pdf, source, target)
    report = inspect_workspace(workspace)
    assert report["source_language"] == source
    assert plan_workspace(workspace)["pending"] > 0
    translated = translate_workspace(workspace, MockTranslationProvider(), batch_size=2)
    assert translated["status"] == "completed"
    before = status_workspace(workspace)["provider_calls"]
    resumed = translate_workspace(workspace, MockTranslationProvider(), batch_size=2)
    assert resumed["api_calls"] == 0
    assert status_workspace(workspace)["provider_calls"] == before
    result = build_workspace(workspace, formats)
    assert set(result["roles"]) == set(OUTPUT_ROLES)
    assert result["validation"]["valid"]
    manifest = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))
    assert manifest["language_pair"] == f"{source}-{target}"
    assert "big-game" not in json.dumps(result)
    return workspace


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_six_language_unicode_and_full_output_smoke(tmp_path: Path, language: str) -> None:
    target = "en" if language != "en" else "fr"
    workspace = _lifecycle(tmp_path, language, target, formats=("md", "docx", "pdf"))
    source_md = next((workspace / "output/source").glob("*.md")).read_text("utf-8")
    assert source_md.strip()
    for role in OUTPUT_ROLES:
        assert next((workspace / f"output/{role}").glob("*.docx")).is_file()
        assert next((workspace / f"output/{role}").glob("*.pdf")).is_file()


@pytest.mark.parametrize(("source", "target"), list(permutations(SUPPORTED_LANGUAGES, 2)))
def test_all_thirty_directed_language_pairs_mock_lifecycle(tmp_path: Path, source: str, target: str) -> None:
    workspace = _lifecycle(tmp_path, source, target)
    assert (workspace / "cache" / f"{source}-{target}").is_dir()


def test_language_detection_and_rejection() -> None:
    assert detect_language(SAMPLES["fr"] * 20) == "fr"
    assert detect_language(SAMPLES["de"] * 20) == "de"
    assert detect_language(SAMPLES["ja"] * 20) == "ja"
    assert detect_language(SAMPLES["es"] * 20) == "es"
    assert detect_language(SAMPLES["zh-Hans"] * 20) == "zh-Hans"
    with pytest.raises(ValueError, match="unsupported language"):
        validate_language("xx")


def test_non_mock_transport_call_accounting_migrates_old_batch_logs(tmp_path: Path) -> None:
    pdf = tmp_path / "source.pdf"; _pdf(pdf, SAMPLES["fr"], "fr")
    workspace = tmp_path / "workspace"; create_workspace(workspace, pdf, "fr", "de"); inspect_workspace(workspace)
    result = translate_workspace(workspace, MockTranslationProvider(), provider_name="deepseek_translation", batch_size=8)
    assert result["api_calls"] == 1
    assert status_workspace(workspace)["provider_calls"] == 1


def test_manual_review_export_has_language_neutral_template_and_ocr(tmp_path: Path) -> None:
    objects = tmp_path / "objects.json"
    objects.write_text(json.dumps({"objects": [{"object_id": "x", "source_page": 1,
        "source_file_sha256": "a" * 64, "source_language": "fr", "target_language": "de",
        "source_text": "déjà", "translated_text": "bereits"}]}), "utf-8")
    out = tmp_path / "review"
    export_review_package(objects, out)
    assert (out / "manual_patch.json").is_file()
    assert (out / "terminology.json").is_file()
    assert (out / "place_names.json").is_file()
    assert "déjà" in (out / "current_ocr.md").read_text("utf-8")


def test_imported_manual_overlay_is_used_by_renderer(tmp_path: Path) -> None:
    workspace = _lifecycle(tmp_path, "fr", "de")
    unit = json.loads((workspace / "data/translation_units.jsonl").read_text("utf-8").splitlines()[0])
    overlay = {"objects": [{"object_id": unit["source_object_id"], "source_text": unit["source_text"],
                            "translated_text": "MANUELL GEPRÜFT"}]}
    (workspace / "manual_review/imported_objects.json").write_text(json.dumps(overlay), "utf-8")
    build_workspace(workspace, ("md",))
    target = next((workspace / "output/target").glob("*.md")).read_text("utf-8")
    assert "MANUELL GEPRÜFT" in target


def test_pdf_renderer_uses_separate_non_overlapping_visual_lines(tmp_path: Path) -> None:
    workspace = _lifecycle(tmp_path, "fr", "zh-Hans", formats=("pdf",))
    pdf_path = next((workspace / "output/bilingual").glob("*.pdf"))
    pdf = fitz.open(pdf_path)
    for page in pdf:
        lines = [line["bbox"] for block in page.get_text("dict")["blocks"] if "lines" in block for line in block["lines"]]
        ordered = sorted((box[1], box[3]) for box in lines)
        assert all(ordered[i + 1][0] >= ordered[i][1] - 1 for i in range(len(ordered) - 1))
    pdf.close()
