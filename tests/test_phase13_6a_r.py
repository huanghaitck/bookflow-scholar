from __future__ import annotations

import http.client
import json
import threading
import zipfile
from pathlib import Path

import fitz
import pytest

from bookflow.credential_store import CredentialStore, resolve_secret
from bookflow.hardcoding_audit import audit_runtime
from bookflow.manual_review import export_review_package
from bookflow.provider_registry import (
    ProviderProfile,
    ProviderSchemaError,
    _normalize_unit_translation,
    _segmented_note_contract,
    _validate_preserved_placeholders,
    _validate_translation_fidelity,
)
from bookflow.ui_server import Handler, SESSION_TOKEN
from bookflow.multilingual_workspace import (
    _publication_clean_values,
    build_workspace,
    create_workspace,
    inspect_workspace,
    rebuild_structured_translation_units,
    translate_workspace,
)
from bookflow.publication_structure import _normalize_record
from bookflow.publication_notes import (
    annotate_note_references,
    classify_note_blocks,
    find_note_reference_labels,
    note_id,
    normalize_note_label,
    split_note_entries,
)
from bookflow.providers.mock import MockTranslationProvider
from http.server import ThreadingHTTPServer


def test_process_credential_never_returns_secret() -> None:
    store = CredentialStore()
    secret = "test-secret-value-not-for-persistence"
    result = store.set("phase13-test", secret, process_only=True)
    assert secret not in json.dumps(result)
    assert resolve_secret(alias="phase13-test") == secret
    assert store.test("phase13-test")["present"] is True
    assert store.delete("phase13-test")["deleted"] is True


def test_provider_public_profile_uses_alias_without_secret_leak() -> None:
    store = CredentialStore()
    store.set("profile-test", "private-token", process_only=True)
    profile = ProviderProfile("p", "openai-compatible", "configurable-model",
                              api_key_alias="profile-test", capabilities=("text",))
    public = profile.public_dict()
    assert public["credential_present"] is True
    assert "private-token" not in json.dumps(public)
    store.delete("profile-test")


def test_translation_fidelity_rejects_source_echo_and_script_leakage() -> None:
    unit = {
        "source_language": "zh-Hans",
        "target_language": "de",
        "source_text": "Aster 的位置没有移动；Cedar 只更换了传感器。",
    }
    with pytest.raises(ProviderSchemaError, match="source echo"):
        _validate_translation_fidelity(unit, unit["source_text"])
    with pytest.raises(ProviderSchemaError, match="source-script leakage"):
        _validate_translation_fidelity(unit, "Aster 的位置没有移动，Cedar 传感器 ersetzt.")
    _validate_translation_fidelity(
        unit,
        "Asters Position blieb unverändert; Cedar ersetzte lediglich den Sensor.",
    )


def test_translation_fidelity_requires_target_script_for_long_latin_source() -> None:
    unit = {
        "source_language": "en",
        "target_language": "ja",
        "source_text": "The station moved east and the table uses the new position.",
    }
    with pytest.raises(ProviderSchemaError, match="target script is absent"):
        _validate_translation_fidelity(unit, "The station moved east and uses the new position.")
    _validate_translation_fidelity(unit, "観測所は東へ移動し、表は新しい位置を使用する。")


def test_translation_fidelity_allows_possessive_suffix_on_protected_term() -> None:
    unit = {
        "source_language": "en", "target_language": "de",
        "source_text": "Birch's reading applies to the new position.",
        "protected_terms": ("Birch",),
    }
    _validate_translation_fidelity(unit, "Birchs Messwert gilt für die neue Position.")
    with pytest.raises(ProviderSchemaError, match="protected term changed"):
        _validate_translation_fidelity(unit, "Der Messwert der Birke gilt für die neue Position.")


def test_complete_review_export_contract(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    document = fitz.open(); document.new_page().insert_text((72, 72), "Source page"); document.save(source); document.close()
    objects = tmp_path / "objects.json"
    objects.write_text(json.dumps({"objects": [{"object_id": "page-1-text", "source_page": 1,
        "source_file_sha256": "a" * 64, "source_text": "Source", "translated_text": "Target"}]}), "utf-8")
    output = tmp_path / "review"
    export_review_package(objects, output, source_pages_pdf=source, complete=True)
    required = {"manifest.json", "instructions.md", "source_pages.pdf", "source_page_images",
                "object_crops", "current_ocr.md", "current_translation.md", "current_structure.json",
                "requested_schema.json", "manual_patch.template.json", "terminology.csv",
                "place_names.csv", "copyable_web_prompt.md"}
    assert required <= {item.name for item in output.iterdir()}


def test_general_runtime_has_no_current_book_hardcoding() -> None:
    package = Path(__file__).parents[1] / "src/bookflow"
    result = audit_runtime(package)
    assert result["production_hardcoding_findings"] == 0, result["findings"]


def test_ui_contains_all_operational_views_and_no_demo_metrics() -> None:
    root = Path(__file__).parents[1] / "src/bookflow/ui_prototypes"
    html = (root / "option_A/index.html").read_text("utf-8")
    for view in ("create", "workflow", "providers", "credentials", "renderer", "review",
                 "webreview", "mapping", "output", "wizard"):
        assert f'data-view="{view}"' in html
    for prohibited in ("306/374", "26/32", "18 review", "82%", "D:\\books\\source.pdf"):
        assert prohibited not in html
    script = (root / "assets/app.js").read_text("utf-8")
    assert "alert(" not in script
    assert "/api/credentials/set" in script and "/api/review/import" in script


def test_ui_post_requires_token_and_never_echoes_secret() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        body = json.dumps({"alias": "ui-test", "secret": "ui-private", "process_only": True})
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
        connection.request("POST", "/api/credentials/set", body, {"Content-Type": "application/json"})
        assert connection.getresponse().status == 403
        connection.request("POST", "/api/credentials/set", body,
                           {"Content-Type": "application/json", "X-Bookflow-Token": SESSION_TOKEN})
        response = connection.getresponse()
        payload = response.read().decode("utf-8")
        assert response.status == 200 and "ui-private" not in payload
        CredentialStore().delete("ui-test")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_publication_mode_embeds_classified_visual_object(tmp_path: Path) -> None:
    source = tmp_path / "visual.pdf"
    document = fitz.open(); page = document.new_page(); page.insert_text((72, 72), "Chapter I\nSource text")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 50), False); pix.clear_with(0x336699)
    page.insert_image(fitz.Rect(72, 120, 240, 230), pixmap=pix); document.save(source); document.close()
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "en", "fr")
    inspect_workspace(workspace); translate_workspace(workspace, MockTranslationProvider())
    classification = {"physical_page": 1, "images": True, "captions": False,
                      "visual_regions": [{"type": "photo", "bbox": [0.12, 0.14, 0.41, 0.28],
                                          "caption_bbox": None, "confidence": 0.96}]}
    (workspace / "data/page_classification.jsonl").write_text(json.dumps(classification) + "\n", "utf-8")
    (workspace / "data/publication_reconstruction.json").write_text(json.dumps({"routes": []}), "utf-8")
    result = build_workspace(workspace, ("md", "docx"), layout_mode="publication")
    assert all(value["visual_object_count"] == 1 for value in result["roles"].values())
    for role in ("source", "target", "bilingual"):
        docx = Path(result["roles"][role]["outputs"]["docx"]["path"])
        with zipfile.ZipFile(docx) as archive:
            assert any(name.startswith("word/media/") for name in archive.namelist())


def test_publication_mode_never_falls_back_to_full_page_table_facsimile(tmp_path: Path) -> None:
    source = tmp_path / "table.pdf"
    document = fitz.open(); page = document.new_page(); page.insert_text((72, 72), "Column A    Column B\nOne         Two")
    document.save(source); document.close()
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "en", "fr")
    inspect_workspace(workspace); translate_workspace(workspace, MockTranslationProvider())
    classification = {"physical_page": 1, "images": False, "captions": False,
                      "tables": True, "footnotes": False}
    (workspace / "data/page_classification.jsonl").write_text(json.dumps(classification) + "\n", "utf-8")
    (workspace / "data/publication_reconstruction.json").write_text(json.dumps({"routes": []}), "utf-8")
    result = build_workspace(workspace, ("md", "docx"), layout_mode="publication")
    assert all(value["visual_object_count"] == 0 for value in result["roles"].values())
    assert all(value["full_page_facsimile_count"] == 0 for value in result["roles"].values())
    for role in ("source", "target", "bilingual"):
        docx = Path(result["roles"][role]["outputs"]["docx"]["path"])
        with zipfile.ZipFile(docx) as archive:
            assert not any(name.startswith("word/media/") for name in archive.namelist())


def test_visual_region_validation_rejects_full_page_and_candidate_mismatch() -> None:
    mechanical = {"landscape": False, "candidate_visual_regions": [{"bbox": [0.1, 0.1, 0.5, 0.5]}]}
    raw = {"page_class": "body", "confidence": 0.95, "reading_order": [], "images": True,
           "visual_regions": [
               {"type": "photo", "bbox": [0.01, 0.01, 0.99, 0.99], "confidence": 0.99},
               {"type": "photo", "bbox": [0.7, 0.7, 0.9, 0.9], "confidence": 0.99},
           ]}
    result = _normalize_record(raw, 1, mechanical)
    assert result["visual_regions"] == []
    assert result["review_required"] is True
    assert "full_page_visual_rejected" in result["review_reason"]
    assert "visual_bbox_mismatch" in result["review_reason"]


def test_structured_units_separate_running_regions_and_exclude_visual_text(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    document = fitz.open()
    for page_number in (1, 2):
        page = document.new_page()
        page.insert_text((72, 35), "ACADEMIC JOURNAL")
        page.insert_text((72, 120), f"Body paragraph on page {page_number}.")
        page.insert_text((80, 240), "MAP LABEL THAT MUST NOT BE TRANSLATED")
        page.insert_text((72, 390), "Figure 1. Study area")
        page.insert_text((72, 700), "1 This is a separately translated footnote.", fontsize=7)
        page.insert_text((72, 815), "Copyright notice")
    document.save(source); document.close()
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "en", "zh-Hans")
    inspect_workspace(workspace)
    records = []
    for page_number in (1, 2):
        records.append({"physical_page": page_number, "page_class": "body",
                        "semantic_regions": [
                            {"type": "header", "bbox": [0.08, 0.01, 0.92, 0.08], "confidence": 0.99},
                            {"type": "footnote", "bbox": [0.08, 0.80, 0.92, 0.88], "confidence": 0.99},
                            {"type": "footer", "bbox": [0.08, 0.94, 0.92, 0.99], "confidence": 0.99},
                        ],
                        "visual_regions": [{"type": "map", "bbox": [0.08, 0.20, 0.92, 0.44],
                                            "caption_bbox": [0.08, 0.44, 0.92, 0.50],
                                            "publication_role": "body_figure", "confidence": 0.99}]})
    (workspace / "data/page_classification.jsonl").write_text(
        "\n".join(json.dumps(item) for item in records) + "\n", "utf-8")
    (workspace / "data/publication_reconstruction.json").write_text(json.dumps({"routes": []}), "utf-8")
    report = rebuild_structured_translation_units(workspace)
    units = [json.loads(line) for line in (workspace / "data/translation_units.jsonl").read_text("utf-8").splitlines()]
    assert {"body", "header", "footer", "footnote", "figure_caption"} <= {unit["element_type"] for unit in units}
    assert all("MAP LABEL" not in unit["source_text"] for unit in units)
    assert report["visual_text_blocks_excluded"] == 2
    translate_workspace(workspace, MockTranslationProvider())
    built = build_workspace(workspace, ("md", "docx", "pdf"), layout_mode="publication")
    assert all(item["anchored_layout"] is True for item in built["roles"].values())
    assert all(item["full_page_facsimile_count"] == 0 for item in built["roles"].values())


def test_cover_and_copyright_illustrations_are_not_body_figures() -> None:
    mechanical = {"landscape": False, "candidate_visual_regions": [{"bbox": [0.1, 0.1, 0.5, 0.5]}]}
    raw = {"page_class": "copyright", "confidence": 0.98, "reading_order": [], "images": True,
           "visual_regions": [{"type": "illustration", "bbox": [0.1, 0.1, 0.5, 0.5],
                               "publication_role": "body_figure", "confidence": 0.98}]}
    assert _normalize_record(raw, 1, mechanical)["visual_regions"] == []


def test_superscript_note_reference_is_not_promoted_to_footnote_region() -> None:
    mechanical = {"landscape": False, "candidate_visual_regions": [],
                  "candidate_semantic_regions": []}
    raw = {"page_class": "body", "confidence": 0.99, "reading_order": ["body"],
           "footnotes": True, "semantic_regions": [
               {"type": "footnote", "bbox": [0.61, 0.72, 0.62, 0.74], "confidence": 0.99},
           ]}
    result = _normalize_record(raw, 4, mechanical)
    assert result["footnotes"] is False
    assert result["note_reference_only"] is True
    assert result["semantic_regions"] == []
    assert result["review_required"] is False


def test_vlm_bottom_strip_without_small_print_candidate_is_not_a_footnote() -> None:
    mechanical = {"landscape": False, "text_characters": 2200, "candidate_visual_regions": [],
                  "candidate_semantic_regions": [
                      {"type": "footer", "bbox": [0.08, 0.61, 0.91, 0.94], "confidence": 0.8},
                  ]}
    raw = {"page_class": "body", "confidence": 0.99, "reading_order": ["body"],
           "footnotes": True, "semantic_regions": [
               {"type": "footnote", "bbox": [0.09, 0.90, 0.90, 0.95], "confidence": 0.99},
           ]}
    result = _normalize_record(raw, 15, mechanical)
    assert result["footnotes"] is False
    assert result["note_reference_only"] is True
    assert result["semantic_regions"] == []


def test_publication_cleanup_removes_repeated_edge_headers_and_adjacent_page_numbers() -> None:
    values = [
        (f"{page}\nJOURNAL OF TRAVEL\nBody text for page {page}",
         f"{page}\n旅行日记\n第 {page} 页正文", page)
        for page in range(1, 5)
    ]
    cleaned, audit = _publication_clean_values(values)
    assert all("JOURNAL OF TRAVEL" not in source for source, _, _ in cleaned)
    assert all("旅行日记" not in target for _, target, _ in cleaned)
    assert all(not source.lstrip().startswith(str(page)) for source, _, page in cleaned)
    assert audit["source_artifact_lines_removed"] == 4
    assert audit["source_page_markers_removed"] == 4
    assert audit["target_artifact_lines_removed"] == 4
    assert audit["target_page_markers_removed"] == 4


def test_publication_cleanup_preserves_front_matter_and_chapter_openings() -> None:
    values = [
        ("BOOK TITLE\nAuthor", "书名\n作者", 1),
        ("BOOK TITLE\nImprint", "书名\n出版信息", 2),
        ("CHAPTER I\nBOOK TITLE\nOpening", "第一章\n书名\n开篇", 3),
        ("4\nBOOK TITLE\nBody", "4\n书名\n正文", 4),
        ("5\nBOOK TITLE\nBody", "5\n书名\n正文", 5),
    ]
    classifications = [
        {"physical_page": 1, "page_class": "cover", "chapter_boundary": False},
        {"physical_page": 2, "page_class": "copyright", "chapter_boundary": False},
        {"physical_page": 3, "page_class": "body", "chapter_boundary": True},
        {"physical_page": 4, "page_class": "body", "chapter_boundary": False},
        {"physical_page": 5, "page_class": "body", "chapter_boundary": False},
    ]
    cleaned, _ = _publication_clean_values(values, classifications)
    assert cleaned[0][0].startswith("BOOK TITLE")
    assert cleaned[1][0].startswith("BOOK TITLE")
    assert "BOOK TITLE" in cleaned[2][0]


def test_document_endnotes_are_independent_units_with_round_trip_links(tmp_path: Path) -> None:
    source = tmp_path / "article.pdf"
    document = fitz.open()
    page = document.new_page(); page.insert_text((72, 120), "A documented claim.3 More body text.")
    page = document.new_page(); page.insert_text((72, 90), "NOTES", fontsize=16)
    page.insert_text((72, 150), "3. The independently translated note text.")
    document.save(source); document.close()
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "en", "fr")
    inspect_workspace(workspace)
    records = [{"physical_page": page, "page_class": "body", "semantic_regions": [],
                "visual_regions": []} for page in (1, 2)]
    (workspace / "data/page_classification.jsonl").write_text(
        "\n".join(json.dumps(item) for item in records) + "\n", "utf-8")
    (workspace / "data/publication_reconstruction.json").write_text(json.dumps({"routes": []}), "utf-8")
    report = rebuild_structured_translation_units(workspace)
    units = [json.loads(line) for line in (workspace / "data/translation_units.jsonl").read_text("utf-8").splitlines()]
    body = next(item for item in units if item["element_type"] == "body")
    note = next(item for item in units if item["element_type"] == "document_endnote")
    assert "{{NOTE_REF:3:" in body["source_text"] and body["note_links"][0]["note_id"] == note["note_id"]
    assert note["source_text"] == "The independently translated note text."
    assert report["note_count"] == report["note_reference_count"] == 1
    graph = json.loads((workspace / "data/note_graph.json").read_text("utf-8"))
    assert graph["unresolved_reference_count"] == 0

    translate_workspace(workspace, MockTranslationProvider())
    built = build_workspace(workspace, ("md", "docx", "pdf"), layout_mode="publication")
    source_outputs = built["roles"]["source"]["outputs"]
    markdown = Path(source_outputs["md"]["path"]).read_text("utf-8")
    assert f'href="#{note["note_id"]}"' in markdown
    assert f'id="{note["note_id"]}"' in markdown and "back 1" in markdown
    with zipfile.ZipFile(source_outputs["docx"]["path"]) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    assert "w:bookmarkStart" in xml and "w:hyperlink" in xml
    pdf = fitz.open(source_outputs["pdf"]["path"])
    pdf_text = "\n".join(page.get_text() for page in pdf)
    links = [link for page in pdf for link in page.get_links()]
    pdf.close()
    assert "back 1" in pdf_text and "\ufffd" not in pdf_text and "\x00" not in pdf_text
    assert sum(link.get("kind") == fitz.LINK_GOTO for link in links) >= 2


def test_chapter_endnotes_stop_at_next_chapter_heading() -> None:
    blocks = {
        1: [{"text": "Notes to Chapter 1"}, {"text": "1. First note"}],
        2: [{"text": "CHAPTER 2"}, {"text": "New chapter body"}],
    }
    classifications = {1: {"chapter_boundary": False}, 2: {"chapter_boundary": True}}
    result = classify_note_blocks(blocks, classifications)
    assert result[(1, 1)]["element_type"] == "note_heading"
    assert result[(1, 2)]["element_type"] == "chapter_endnote"
    assert (2, 1) not in result and (2, 2) not in result


def test_translation_contract_rejects_changed_note_placeholders() -> None:
    source = "Text.{{NOTE_REF:3:note-ref-p0001-3-001}}"
    _validate_preserved_placeholders(source, source)
    with pytest.raises(ProviderSchemaError):
        _validate_preserved_placeholders(source, "Texte.3")
    contract = _segmented_note_contract(source + " More text.")
    assert contract and contract["segments"] == ["Text.", " More text."]
    unit = {"source_text": source + " More text.", "source_language": "en", "target_language": "fr"}
    result = _normalize_unit_translation(
        {"translated_segments": [{"id": 0, "text": "Texte."}, {"id": 1, "text": "Suite."}]}, unit)
    assert result.translated_text == "Texte.{{NOTE_REF:3:note-ref-p0001-3-001}}Suite."
    with pytest.raises(ProviderSchemaError):
        _normalize_unit_translation({"translated_segments": [{"id": 0, "text": "Texte."}]}, unit)


def test_symbol_footnote_reference_gets_a_safe_round_trip_anchor() -> None:
    source = "Measured change +3.2 \u2020 with context."
    annotated, links = annotate_note_references(
        source, {"\u2020": "page-0002-footnote-u2020"}, page_no=2, occurrence_counts={},
    )
    assert find_note_reference_labels(source) == ["\u2020"]
    assert "{{NOTE_REF:\u2020:" in annotated
    assert links[0]["reference_id"] == "note-ref-p0002-u2020-001"
    assert note_id("footnote", "page-0002-footnotes", "\u2020").endswith("-u2020")
    _validate_preserved_placeholders(annotated, annotated)


def test_endnote_page_number_sentence_is_not_promoted_to_new_note(tmp_path: Path) -> None:
    source = tmp_path / "notes.pdf"
    document = fitz.open(); page = document.new_page()
    page.insert_text((72, 72), "NOTES", fontsize=16)
    page.insert_text((72, 120), "9. Earlier note cites page 23.\n212. For more background, see the archive.\n10. Next note.")
    document.save(source); document.close()
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "en", "de")
    inspect_workspace(workspace)
    record = {"physical_page": 1, "page_class": "body", "semantic_regions": [], "visual_regions": []}
    (workspace / "data/page_classification.jsonl").write_text(json.dumps(record) + "\n", "utf-8")
    report = rebuild_structured_translation_units(workspace)
    units = [json.loads(line) for line in (workspace / "data/translation_units.jsonl").read_text("utf-8").splitlines()]
    labels = {item.get("note_label") for item in units if item.get("note_label")}
    assert labels == {"9", "10"}
    assert "212. For more background" in next(item["source_text"] for item in units if item.get("note_label") == "9")
    assert report["note_numbering_anomaly_count"] == 1


def test_note_number_ocr_repairs_require_sequence_context() -> None:
    entries = split_note_entries("ii. Eleventh note.\n12. Twelfth note.\n4o. Fortieth note.")
    assert [label for label, _ in entries] == ["ii", "12", "4o"]
    assert normalize_note_label("ii", previous_numeric=10) == "11"
    assert normalize_note_label("ii", previous_numeric=1) == "2"
    assert normalize_note_label("4o", previous_numeric=39) == "40"
    assert normalize_note_label("6i", previous_numeric=60) == "61"
