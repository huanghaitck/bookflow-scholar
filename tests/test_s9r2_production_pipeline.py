from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import fitz
import pytest

from bookflow.batch_backend import BatchBackend
from bookflow.multilingual_workspace import (
    _attach_page_markers_and_translation_groups,
    _anchored_publication_pages,
    _merge_wrapped_text_blocks,
    _normalize_ocr_markdown_table,
    _ocr_fallback_blocks,
    _original_page_marker_label,
    _unit_render_parts,
    edition_output_stem,
    translate_workspace,
)
from bookflow.page_quality import OCRRouter, PageTextQualityGate, PageTextQualityResult
from bookflow.provider_registry import (
    ConfiguredModelClient,
    ProviderSchemaError,
    ProviderProfile,
    RegistryTranslationProvider,
    _normalize_unit_translation,
    _mask_protected_terms,
    _protected_document_terms,
    _protected_literal_terms,
    _protected_term_markers,
    _restore_protected_terms,
    _structured_table_contract,
    _validate_translation_fidelity,
    _workspace_protected_document_terms,
)


PYTHON = Path(sys.executable)


def fixture_pdf(path: Path) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((50, 70), "A language-neutral production document with enough readable text. " * 5)
    document.save(path)
    document.close()
    return path


def test_page_quality_gate_is_explainable() -> None:
    gate = PageTextQualityGate()
    good = gate.evaluate(page_id="page-0001", text="Readable publication text with words and spaces. " * 5)
    bad = gate.evaluate(page_id="page-0002", text="\ufffd\ufffd\ufffd\x00AAAAAAAAAAAA")
    assert good.passed and good.recommended_route == "python_text"
    assert not bad.passed and bad.recommended_route == "ocr_router"
    assert bad.issue_codes and 0 <= bad.quality_score <= 1


def test_raster_page_ocr_is_split_into_translatable_structural_units() -> None:
    blocks = _ocr_fallback_blocks(
        "Results\n\n| item | value |\n|---|---|\n| sample | 3 |\n\n"
        "\u2020 Measurement note.\n\nInterpret the table with the preceding paragraph.\n\n2",
        {"semantic_regions": [
            {"type": "header", "bbox": [0.1, 0.04, 0.9, 0.09]},
            {"type": "footnote", "bbox": [0.08, 0.68, 0.92, 0.75]},
        ], "visual_regions": [{"type": "table", "bbox": [0.05, 0.12, 0.95, 0.55]}]},
    )
    assert [item["ocr_element_type"] for item in blocks] == [
        "header", "body", "footnote", "body", "pagination",
    ]
    assert blocks[1]["source"] == "ocr_table"


def test_ragged_multilevel_ocr_table_is_flattened_to_equal_columns() -> None:
    table = _normalize_ocr_markdown_table(
        "| Site | Position | Reading | Change | Note |\n"
        "|---|---|---|---|---|\n"
        "| | | AM | PM | |\n"
        "| Aster | fixed | 18.4 | 18.1 | -0.3 | stable |"
    )
    rows = [line.strip().strip("|").split("|") for line in table.splitlines()]
    assert {len(row) for row in rows} == {6}
    assert "Reading AM" in table and "Reading PM" in table
    assert len(rows) == 3


def test_adjacent_geometric_line_blocks_merge_without_crossing_paragraph_gap() -> None:
    blocks = [
        {"text": "The first line ends with the", "bbox": [0.1, 0.10, 0.9, 0.12], "font_size": 11.0},
        {"text": "same paragraph continuation.", "bbox": [0.1, 0.123, 0.8, 0.143], "font_size": 11.0},
        {"text": "A new paragraph.", "bbox": [0.1, 0.20, 0.6, 0.22], "font_size": 11.0},
    ]
    merged = _merge_wrapped_text_blocks(blocks)
    assert [item["text"] for item in merged] == [
        "The first line ends with the same paragraph continuation.",
        "A new paragraph.",
    ]


def test_centered_wrapped_caption_lines_merge_despite_different_left_edges() -> None:
    blocks = [
        {"text": "Figure 1. A long centered caption that", "bbox": [0.13, 0.74, 0.87, 0.758], "font_size": 9.0},
        {"text": "continues on a short line.", "bbox": [0.42, 0.759, 0.58, 0.777], "font_size": 9.0},
    ]
    assert _merge_wrapped_text_blocks(blocks)[0]["text"].endswith("continues on a short line.")


def test_glm_vision_request_uses_bare_base64_without_response_format(tmp_path: Path) -> None:
    captured: dict = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"model_dump": lambda self, mode: {"choices": []}})()

    profile = ProviderProfile(provider_id="glm", provider_type="openai_compatible", model="glm-4.6v",
                              base_url="https://example.invalid", api_key_env="TEST_KEY",
                              api_key_alias="test", capabilities=("vision",))
    client = object.__new__(ConfiguredModelClient)
    client.profile = profile
    client.sdk = type("SDK", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    client._last_call = 0.0
    image = tmp_path / "page.png"; image.write_bytes(b"png-bytes")
    ledger_path = tmp_path / "provider_attempts.jsonl"
    client.vision_json(
        prompt="return json", image_path=image, attempt_ledger_path=ledger_path,
        attempt_context={"project_id": "project-dynamic", "job_id": "job-dynamic",
                         "stage": "structure", "provider_role": "vision",
                         "page_or_segment_id": "page-1", "purpose": "layout-analysis"},
    )
    request_url = captured["messages"][0]["content"][1]["image_url"]["url"]
    assert not request_url.startswith("data:")
    assert "response_format" not in captured
    attempts = [json.loads(line) for line in ledger_path.read_text("utf-8").splitlines()]
    assert [item["status"] for item in attempts] == ["dispatching", "succeeded"]
    assert attempts[-1]["project_id"] == "project-dynamic"
    assert attempts[-1]["request_hash"] and attempts[-1]["response_hash"]
    assert "return json" not in ledger_path.read_text("utf-8")


def test_translation_disables_thinking_and_retries_invalid_saved_raw(tmp_path: Path) -> None:
    captured: dict = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            payload = {"choices": [{"message": {"content": '{"translated_text":"译文"}'}}]}
            return type("Response", (), {"model_dump": lambda self, mode: payload})()

    profile = ProviderProfile(provider_id="text", provider_type="openai_compatible", model="deepseek-v4-pro",
                              base_url="https://example.invalid", api_key_env="TEST_KEY",
                              api_key_alias="test", capabilities=("text",))
    client = object.__new__(ConfiguredModelClient)
    client.profile = profile
    client.sdk = type("SDK", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    client._last_call = 0.0
    unit = {"translation_unit_id": "tu_test", "source_text_sha256": "a" * 64,
            "source_language": "en", "target_language": "zh-Hans", "source_text": "source"}
    fingerprint = __import__("hashlib").sha256(json.dumps({
        "unit": unit["translation_unit_id"], "source_sha256": unit["source_text_sha256"],
        "provider": profile.provider_id, "model": profile.model,
        "source_language": unit["source_language"], "target_language": unit["target_language"],
    }, sort_keys=True).encode()).hexdigest()
    raw_path = tmp_path / "logs/text/raw/failed.json"; raw_path.parent.mkdir(parents=True)
    raw_path.write_text('{"choices":[{"finish_reason":"length","message":{"content":""}}]}', "utf-8")
    attempts = tmp_path / "logs/text_attempts.jsonl"
    attempts.write_text(json.dumps({"fingerprint": fingerprint, "status": "raw_saved",
                                    "raw_path": str(raw_path), "translation_unit_id": "tu_test"}) + "\n", "utf-8")
    result = RegistryTranslationProvider(tmp_path, client).translate_batch([unit])
    assert result[0]["translated_text"] == "译文"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_translation_quality_retry_reissues_only_the_rejected_unit(tmp_path: Path) -> None:
    calls: list[dict] = []

    class Client:
        profile = ProviderProfile(
            provider_id="text", provider_type="openai_compatible", model="generic-model",
            max_retries=2, capabilities=("text",),
        )
        last_request_metrics = {
            "start": "2026-01-01T00:00:00+00:00", "end": "2026-01-01T00:00:01+00:00",
            "latency": 1.0, "retry_number": 0, "usage": {}, "status": "succeeded",
        }

        def text_json(self, **kwargs):
            calls.append(kwargs)
            translated = (
                "观测站向东移动，表格使用新位置。" if len(calls) == 1
                else "Die Beobachtungsstation bewegte sich nach Osten; die Tabelle verwendet die neue Position."
            )
            return {"choices": [{"message": {"content": json.dumps(
                {"translated_text": translated}, ensure_ascii=False,
            )}}]}

    unit = {
        "translation_unit_id": "tu_dynamic", "source_text_sha256": "b" * 64,
        "source_language": "zh-Hans", "target_language": "de",
        "source_text": "观测站向东移动，表格使用新位置。",
    }
    result = RegistryTranslationProvider(tmp_path, Client()).translate_batch([unit])
    assert len(calls) == 2
    assert calls[1]["payload"]["quality_retry"]["attempt"] == 1
    assert calls[1]["attempt_context"]["purpose"].endswith("quality_retry_1")
    assert result[0]["translated_text"].startswith("Die Beobachtungsstation")


def test_structured_table_translation_segments_cells_and_preserves_note_marker() -> None:
    marker = "{{NOTE_REF:†:note-ref-dynamic}}"
    source = (
        "| Site | Status | Change |\n| --- | --- | --- |\n"
        f"| Birch | New position | +3.2 {marker} |"
    )
    unit = {
        "source_language": "en", "target_language": "es", "source_text": source,
        "preserve_structure": True,
    }
    contract = _structured_table_contract(source)
    assert contract is not None
    assert marker not in json.dumps(contract["source_segments"], ensure_ascii=False)
    translated = {
        "translated_segments": [
            {"id": item["id"], "text": f"celda-{item['id']}"}
            for item in contract["source_segments"]
        ]
    }
    result = _normalize_unit_translation(translated, unit)
    assert marker in result.translated_text
    assert [line.count("|") for line in result.translated_text.splitlines()] == [4, 4, 4]


def test_repeated_table_identifiers_become_document_protected_terms() -> None:
    units = [
        {"source_text": "The Northstar marker moved east.", "preserve_structure": False},
        {"source_text": "| Site | Status |\n|---|---|\n| Northstar | moved |", "preserve_structure": True},
        {"source_text": "Northstar is used again in the note.", "preserve_structure": False},
    ]
    assert _protected_document_terms(units) == ("Northstar",)


def test_markdown_table_identifiers_do_not_require_a_preserve_structure_hint() -> None:
    units = [
        {"source_text": "Northstar", "preserve_structure": False},
        {"source_text": "| Site | Status |\n|---|---|\n| Northstar | moved |"},
        {"source_text": "The Northstar marker appears in the diagram."},
    ]
    assert _protected_document_terms(units) == ("Northstar",)


def test_protected_terms_use_the_whole_workspace_when_batches_are_single_units(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "data").mkdir(parents=True)
    document_units = [
        {"source_text": "Northstar"},
        {"source_text": "| Site | Status |\n|---|---|\n| Northstar | moved |"},
        {"source_text": "Northstar appears in the diagram."},
    ]
    (workspace / "data" / "translation_units.jsonl").write_text(
        "".join(json.dumps(unit) + "\n" for unit in document_units), "utf-8",
    )
    assert _workspace_protected_document_terms(
        workspace, [document_units[0]],
    ) == ("Northstar",)


def test_protected_term_placeholders_preserve_names_but_leave_suffixes_translatable() -> None:
    markers = _protected_term_markers(("Northstar",))
    masked = _mask_protected_terms("Northstar-old\nNorthstar-new", markers)
    assert masked == "{{PROTECTED_TERM:0}}-old\n{{PROTECTED_TERM:0}}-new"
    translated = "{{PROTECTED_TERM:0}}-ancien\n{{PROTECTED_TERM:0}}-nouveau"
    assert _restore_protected_terms(translated, markers) == (
        "Northstar-ancien\nNorthstar-nouveau"
    )


def test_table_heading_words_are_not_document_protected_terms() -> None:
    units = [
        {"source_text": "Messwerte der Stationen und Standortstatus", "preserve_structure": False},
        {
            "source_text": (
                "| Station | Standortstatus | Vormittag |\n"
                "|---|---|---|\n"
                "| Northstar | Unverändert | 18.4 |"
            ),
            "preserve_structure": True,
        },
        {"source_text": "Northstar wird in der Anmerkung erneut verwendet.", "preserve_structure": False},
    ]
    assert _protected_document_terms(units) == ("Northstar",)


def test_exact_protected_identifier_is_not_rejected_as_source_echo() -> None:
    unit = {
        "source_language": "en",
        "target_language": "fr",
        "source_text": "Northstar",
        "protected_terms": ("Northstar",),
    }
    _validate_translation_fidelity(unit, "Northstar")


def test_url_header_is_a_protected_literal_and_not_rejected_as_source_echo() -> None:
    source = "www.nature.com/scientificreports"
    assert _protected_literal_terms(source) == (source,)
    unit = {
        "source_language": "en",
        "target_language": "zh-Hans",
        "source_text": source,
        "protected_terms": _protected_literal_terms(source),
    }
    _validate_translation_fidelity(unit, source)


def test_url_inside_prose_is_protected_without_exempting_the_prose() -> None:
    source = "See https://example.org/report?id=7 for the complete report."
    assert _protected_literal_terms(source) == ("https://example.org/report?id=7",)
    unit = {
        "source_language": "en",
        "target_language": "zh-Hans",
        "source_text": source,
        "protected_terms": _protected_literal_terms(source),
    }
    with pytest.raises(ProviderSchemaError, match="untranslated source echo"):
        _validate_translation_fidelity(unit, source)


def test_original_page_marker_prefers_detected_label_and_has_generic_fallback() -> None:
    assert _original_page_marker_label("xiv.", 22) == ("xiv", "detected_printed_page")
    assert _original_page_marker_label("", 22) == ("22", "physical_page_fallback")


def test_cross_page_body_uses_one_translation_group_and_reinserts_markers(tmp_path: Path) -> None:
    units = [
        {
            "translation_unit_id": "tu-left", "source_object_id": "obj-left",
            "source_block_id": "block-left", "source_page": 1,
            "source_text": "The sentence continues across the", "source_text_sha256": "left",
            "source_language": "en", "target_language": "de", "element_type": "body",
            "reading_order": [0.5, 0.1],
        },
        {
            "translation_unit_id": "tu-right", "source_object_id": "obj-right",
            "source_block_id": "block-right", "source_page": 2,
            "source_text": "next physical page.", "source_text_sha256": "right",
            "source_language": "en", "target_language": "de", "element_type": "body",
            "reading_order": [0.2, 0.1],
        },
    ]
    layout = [
        {"source_page": 1, "element_type": "body", "translation_unit_id": "tu-left"},
        {"source_page": 1, "element_type": "pagination", "source_text": "10"},
        {"source_page": 2, "element_type": "body", "translation_unit_id": "tu-right"},
        {"source_page": 2, "element_type": "pagination", "source_text": "11"},
    ]
    groups = _attach_page_markers_and_translation_groups(units, layout)
    assert len(groups) == 1
    assert groups[0]["unit_ids"] == ["tu-left", "tu-right"]
    assert "[[BOOKFLOW_PAGE_BREAK:2:11]]" in groups[0]["source_text"]
    assert units[0]["page_markers_before"][0]["printed_page"] == "10"
    assert units[0]["page_markers_after"][0]["printed_page"] == "11"

    workspace = tmp_path / "workspace"
    for directory in ("data", "cache/en-de", "logs", "checkpoints"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    (workspace / "bookflow_workspace.json").write_text(json.dumps({
        "workspace_id": "workspace-test", "language_pair": "en-de",
        "source_language": "en", "target_language": "de", "stage": "inspected",
        "provider_calls": 0,
    }) + "\n", "utf-8")
    (workspace / "data/translation_units.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in units), "utf-8",
    )
    (workspace / "data/translation_groups.jsonl").write_text(
        json.dumps(groups[0]) + "\n", "utf-8",
    )

    class ContextProvider:
        received: list[dict] = []

        def translate_batch(self, values):
            self.received.extend(values)
            return [{
                "translation_unit_id": value["translation_unit_id"],
                "translated_text": value["source_text"].replace(
                    "The sentence continues across the", "Der Satz geht über die"
                ).replace("next physical page.", "nächste physische Seite weiter."),
            } for value in values]

    provider = ContextProvider()
    result = translate_workspace(workspace, provider, provider_name="test", model="context")
    assert result["translated"] == 2
    assert len(provider.received) == 1
    left = json.loads((workspace / "cache/en-de/tu-left.json").read_text("utf-8"))
    right = json.loads((workspace / "cache/en-de/tu-right.json").read_text("utf-8"))
    assert left["translated_text"] == "Der Satz geht über die"
    assert right["translated_text"] == "nächste physische Seite weiter."
    rendered = _unit_render_parts(
        {**units[0], "translated_text": left["translated_text"]}, "target",
        {"source_language": "en", "target_language": "de"},
    )
    assert rendered == [
        ("【10】", "zh-Hans"), ("Der Satz geht über die", "de"), ("【11】", "zh-Hans"),
    ]


def test_render_parts_normalize_font_hostile_compatibility_characters() -> None:
    manifest = {"source_language": "en", "target_language": "zh-Hans"}
    unit = {
        "source_text": "Reference¹⁶ (+ 1266)",
        "translated_text": "文献¹⁹（+ 1266）",
        "preserve_structure": False,
    }
    assert _unit_render_parts(unit, "source", manifest) == [("Reference16 (+ 1266)", "en")]
    assert _unit_render_parts(unit, "target", manifest) == [("文献19(+ 1266)", "zh-Hans")]


def test_protected_identifier_does_not_exempt_an_untranslated_phrase() -> None:
    unit = {
        "source_language": "en",
        "target_language": "fr",
        "source_text": "Northstar moved east",
        "protected_terms": ("Northstar",),
    }
    try:
        _validate_translation_fidelity(unit, "Northstar moved east")
    except ProviderSchemaError:
        pass
    else:
        raise AssertionError("an untranslated phrase must still fail the source-echo gate")


def test_anchored_publication_uses_manual_review_overlay(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "data").mkdir(parents=True)
    (workspace / "cache" / "en-de").mkdir(parents=True)
    (workspace / "manual_review").mkdir(parents=True)
    unit = {
        "translation_unit_id": "tu-1", "source_object_id": "body-1",
        "source_text": "Original source", "source_page": 1,
    }
    (workspace / "data" / "translation_units.jsonl").write_text(json.dumps(unit) + "\n", "utf-8")
    element = {
        "source_page": 1, "element_type": "body", "translation_unit_id": "tu-1",
        "page_sequence": 1,
    }
    (workspace / "data" / "page_layout_elements.jsonl").write_text(json.dumps(element) + "\n", "utf-8")
    (workspace / "cache" / "en-de" / "tu-1.json").write_text(
        json.dumps({"translated_text": "Cached target"}) + "\n", "utf-8",
    )
    (workspace / "manual_review" / "imported_objects.json").write_text(json.dumps({
        "objects": [{"object_id": "body-1", "source_text": "Reviewed source",
                     "translated_text": "Reviewed target"}],
    }) + "\n", "utf-8")
    pages = _anchored_publication_pages(
        workspace, "bilingual",
        {"language_pair": "en-de", "source_language": "en", "target_language": "de"}, {},
    )
    assert pages[1][0]["element_type"] == "original_page_marker"
    assert pages[1][0]["parts"] == [("【1】", "zh-Hans")]
    body = next(item for item in pages[1] if item["element_type"] == "body")
    assert body["parts"] == [("Reviewed source", "en"), ("Reviewed target", "de")]


def test_ocr_provider_failure_routes_to_web_assist(tmp_path: Path) -> None:
    class FailedClient:
        def vision_json(self, **kwargs):
            raise RuntimeError("provider unavailable")

    class Registry:
        def get(self, provider_id, capability):
            return type("Profile", (), {"provider_type": "openai_compatible", "provider_id": "vision"})()

        def client(self, profile):
            return FailedClient()

    doc = fitz.open(); page = doc.new_page(); page.insert_text((50, 70), "fallback source text")
    quality = PageTextQualityResult("page-0001", "pymupdf", 0.2, False, ("low_printable_ratio",), {},
                                    "ocr_router")
    result = OCRRouter(registry=Registry(), vision_provider_id="vision", allow_provider_calls=True).route(
        page=page, page_id="page-0001", extracted_text="fallback source text", quality=quality,
        output_dir=tmp_path,
    )
    doc.close()
    assert result.route == "difficult_page_web_assist"
    assert result.status == "review_required"
    assert "vision_provider_failed" in result.issue_codes


def test_import_without_project_creates_a_generic_active_project(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    source = fixture_pdf(tmp_path / "A normal book.pdf")
    imported = backend.import_sources(
        None, [source], command_id="direct-first-import",
        pipeline_config={"source_language": "en", "target_language": "de"},
    )
    snapshot = backend.snapshot(project_id=imported["project_id"])
    assert snapshot["active_context"]["active_project_id"] == imported["project_id"]
    assert snapshot["active_context"]["active_source_id"] == imported["results"][0]["source_id"]
    assert snapshot["active_context"]["active_job_id"]
    assert snapshot["active_context"]["active_batch_id"] == imported["batch_id"]
    assert snapshot["active_project"]["name"] == "A normal book"


def test_formal_batch_uses_generic_workspace_and_publication_outputs(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    project = backend.create_project("Production adapter")
    source = fixture_pdf(tmp_path / "source.pdf")
    imported = backend.import_sources(project["project_id"], [source], command_id="import",
                                      pipeline_config={"source_language": "en", "target_language": "de",
                                                       "text_provider_id": "mock", "vision_provider_id": "mock",
                                                       "output_formats": ["md", "docx", "pdf"]})
    result = backend.run_batch(imported["batch_id"])
    assert result["counts"] == {"completed": 1}
    job = backend.snapshot(batch_id=imported["batch_id"])["jobs"][0]
    output = Path(job["output_path"])
    assert (output / "source_en.md").is_file()
    assert (output / "source_de.docx").is_file()
    assert (output / "source zweisprachig.pdf").is_file()
    metadata = json.loads((output / "metadata.json").read_text("utf-8"))
    assert metadata["production_pipeline"] == "phase13.5+validated-phase13.6"
    assert (Path(metadata["workspace"]) / "data/page_text_quality.jsonl").is_file()
    assert (Path(metadata["workspace"]) / "data/book_structure.json").is_file()
    assert job["usage"]["whole_book_calls"] == 0


def test_edition_output_names_use_source_and_translated_titles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    cache = workspace / "cache" / "zh-Hans-en"
    data = workspace / "data"
    cache.mkdir(parents=True)
    data.mkdir(parents=True)
    (workspace / "bookflow_workspace.json").write_text(json.dumps({
        "workspace_id": "workspace_dynamic",
        "source_filename": "西游记.pdf",
        "book_title": "西游记",
        "source_language": "zh-Hans",
        "target_language": "en",
        "language_pair": "zh-Hans-en",
    }, ensure_ascii=False), "utf-8")
    (data / "translation_units.jsonl").write_text(json.dumps({
        "translation_unit_id": "tu_title",
        "source_text": "西游记",
        "element_type": "title",
    }, ensure_ascii=False) + "\n", "utf-8")
    (cache / "tu_title.json").write_text(json.dumps({
        "translated_text": "Journey to the West",
    }, ensure_ascii=False), "utf-8")
    assert edition_output_stem(workspace, "source") == "西游记_zh"
    assert edition_output_stem(workspace, "target") == "Journey to the West_en"
    assert edition_output_stem(workspace, "bilingual") == "Journey to the West bilingual"


def test_persistent_sidecar_pid_events_and_graceful_shutdown(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    command = [str(PYTHON), "-m", "bookflow.bridge_cli", "--backend-root", str(tmp_path / "backend"),
               "--provider-config", str(root / "config/providers.local.yaml"), "--persistent"]
    environment = os.environ.copy(); environment["PYTHONPATH"] = str(root / "src")
    process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
    frames: queue.Queue[dict] = queue.Queue()
    assert process.stdout is not None and process.stdin is not None
    threading.Thread(target=lambda: [frames.put(json.loads(line)) for line in process.stdout], daemon=True).start()
    ready = frames.get(timeout=10)
    assert ready["kind"] == "ready" and ready["backend_pid"] == process.pid
    envelope = {"schema_version": "1.2", "contract_version": "1.2.0", "command_id": "create",
                "command": "createProject", "payload": {"name": "Persistent"}}
    process.stdin.write(json.dumps({"kind": "command", "request_id": "create", "envelope": envelope}) + "\n")
    process.stdin.flush()
    kinds: set[str] = set()
    event_seen = False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not ({"response", "heartbeat"} <= kinds and event_seen):
        frame = frames.get(timeout=5); kinds.add(frame["kind"])
        event_seen = event_seen or frame["kind"] == "event"
    assert {"response", "heartbeat"} <= kinds and event_seen and process.poll() is None
    process.stdin.write(json.dumps({"kind": "shutdown", "request_id": "stop"}) + "\n"); process.stdin.flush()
    while frames.get(timeout=15)["kind"] != "stopped":
        pass
    process.wait(timeout=10)
    assert process.returncode == 0
