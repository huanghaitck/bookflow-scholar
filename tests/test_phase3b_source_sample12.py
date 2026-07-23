from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from docx import Document

from bookflow.io_utils import load_json, sha256_file
from bookflow.paths import load_settings, project_root
from bookflow.phase3b_source import (
    Phase3BBoundaryObservation,
    build_phase3b_source_document,
    create_phase3b_sample,
    export_phase3b_source,
    phase3b_preflight,
    resolve_phase3b_boundary,
    run_phase3b_page12,
    run_phase3b_pair_11_12,
    validate_phase3b_scope,
)
from bookflow.vision_provider import ProviderResponse


ROOT = project_root()
SAMPLE11 = ROOT / "input/sample_11_pages.pdf"
PAGE12 = ROOT / "input/sample_page_12.pdf"
FULL_PDF = ROOT / "input/The big game of central and western China (1913).pdf"


def _settings(tmp_path: Path):
    return load_settings().model_copy(
        update={
            "phase3b_source_sample_pdf": str(tmp_path / "sample_12_pages.pdf"),
            "phase3b_data_directory": str(tmp_path / "phase3b"),
            "phase3b_master_path": str(tmp_path / "source_document_sample12_v1.json"),
            "phase3b_diagnostic_directory": str(tmp_path / "diagnostic"),
            "phase3b_final_directory": str(tmp_path / "final"),
        }
    )


def _page_payload(document_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.1",
        "document_id": document_id,
        "pdf_page": 12,
        "provider": "zhipu",
        "model": "glm-4.6v",
        "page_type": "printed",
        "printed_page": "12",
        "title": None,
        "running_header": "SHANGHAI",
        "footer": "Univ Calif - Digitized by Microsoft ®",
        "page_number_text": "12",
        "blocks": [
            {
                "block_id": "1",
                "block_type": "body",
                "order": 1,
                "text": (
                    "King George the fullest justice, ejaculated at no one in particular “Hey! "
                    "Break away. I see you.” Below a group of Loyalists with jovial, brazen voices "
                    "reiterated the statement that they were the Dollar Princesses, the wretchedest "
                    "women on earth. The old gentleman in the next window stopped his everlasting "
                    "“Break away,” and yelled “Banzais” at the top of his voice."
                ),
                "bounding_box": None,
                "confidence": None,
                "uncertain": False,
                "notes": None,
            },
            {
                "block_id": "2",
                "block_type": "body",
                "order": 2,
                "text": (
                    "The brown faces shone duskily in the glare of the lanterns. “Banzai! Banzai!” "
                    "they called back. It began to pour with rain, and Coronation day was over."
                ),
                "bounding_box": None,
                "confidence": None,
                "uncertain": False,
                "notes": None,
            },
        ],
        "continuation_from_previous": True,
        "continuation_to_next": False,
        "boundary_notes": "The first body block continues from the previous page; the final paragraph ends completely.",
        "uncertain_characters": [],
        "warnings": [],
        "status": "technical_validation_only",
        "translation_ready": False,
    }


def _pair_payload(document_id: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "boundary_id": "boundary_p0011_p0012",
        "document_id": document_id,
        "previous_page": 11,
        "next_page": 12,
        "previous_visible_tail": "who had obviously been doing",
        "next_visible_head": "King George the fullest justice",
        "visible_trailing_hyphen": False,
        "suspected_word_continuation": False,
        "suspected_sentence_continuation": True,
        "suspected_paragraph_continuation": True,
        "structural_break": "none",
        "header_footer_interference": False,
        "possible_omission": False,
        "possible_duplication": False,
        "evidence": ["The next page begins with a capitalized continuation that completes the clause."],
        "conflicting_evidence": [],
        "confidence": 0.98,
        "status": "observed",
    }


class FakeProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    def transcribe_images(self, **_: object) -> ProviderResponse:
        self.calls += 1
        content = json.dumps(self.payload, ensure_ascii=False)
        return ProviderResponse(
            content=content,
            usage={"prompt_tokens": 2000, "completion_tokens": 600, "total_tokens": 2600},
            request_id=f"mock-{self.calls}",
            response_model="glm-4.6v",
            raw_response={
                "id": f"mock-{self.calls}",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 2000, "completion_tokens": 600, "total_tokens": 2600},
            },
        )


@pytest.fixture()
def prepared(tmp_path: Path):
    settings = _settings(tmp_path)
    manifest = create_phase3b_sample(settings, root=ROOT)
    return settings, manifest


def test_phase3b_configuration_is_fail_closed() -> None:
    settings = load_settings()
    assert settings.translation_disabled is True
    assert settings.terminology_translation_disabled is True
    assert settings.phase3b_max_single_calls == 1
    assert settings.phase3b_max_pair_calls == 1
    assert settings.phase3b_max_triple_calls == 0
    assert settings.phase3b_max_total_calls == 2
    assert settings.phase3b_automatic_retry is False


def test_create_sample12_preserves_inputs_and_has_twelve_pages(tmp_path: Path) -> None:
    before11 = sha256_file(SAMPLE11)
    before12 = sha256_file(PAGE12)
    settings = _settings(tmp_path)
    manifest = create_phase3b_sample(settings, root=ROOT)
    with fitz.open(manifest.derived_pdf) as pdf:
        assert pdf.page_count == 12
    assert sha256_file(SAMPLE11) == before11
    assert sha256_file(PAGE12) == before12
    assert manifest.sample11_sha256 == before11
    assert manifest.page12_pdf_sha256 == before12


def test_derived_manifest_has_new_document_id_and_reuses_first_eleven(prepared) -> None:
    _, manifest = prepared
    assert manifest.document_id != "doc_c66d2d4a1eb143e0"
    assert manifest.page_count == 12
    assert manifest.reused_visual_pages == list(range(1, 12))
    assert manifest.newly_rendered_pages == [12]
    assert all(item.cache_reused for item in manifest.pages[:11])
    assert manifest.pages[11].cache_reused is False


def test_phase3b_rejects_full_pdf() -> None:
    settings = load_settings()
    with pytest.raises(PermissionError):
        validate_phase3b_scope(FULL_PDF, settings, root=ROOT)


def test_preflight_allows_only_one_single_and_one_pair(prepared) -> None:
    settings, manifest = prepared
    report = phase3b_preflight(settings, manifest=manifest, root=ROOT)
    assert report.single_calls_expected == 1
    assert report.pair_calls_expected == 1
    assert report.triple_calls_allowed == 0
    assert report.maximum_new_calls == 2
    assert report.calls_already_started == 0
    assert report.remaining_real_calls == 2
    assert report.maximum_cash_cost_cny == pytest.approx(0.50)
    assert report.deepseek_calls == 0
    assert report.translation_calls == 0


def test_page12_call_is_cached_without_second_provider_call(prepared) -> None:
    settings, manifest = prepared
    provider = FakeProvider(_page_payload(manifest.document_id))
    first = run_phase3b_page12(settings, manifest, provider=provider, allow_api=True, confirmed=True, root=ROOT)
    second = run_phase3b_page12(settings, manifest, provider=provider, allow_api=True, confirmed=True, root=ROOT)
    assert first.api_calls_started == 1
    assert second.api_calls_started == 0
    assert second.cache_hits == 1
    assert provider.calls == 1
    assert Path(first.raw_response_path).is_file()
    assert Path(first.normalized_output_path).is_file()


def test_pair_call_is_cached_and_never_requests_triple(prepared) -> None:
    settings, manifest = prepared
    page_provider = FakeProvider(_page_payload(manifest.document_id))
    page = run_phase3b_page12(settings, manifest, provider=page_provider, allow_api=True, confirmed=True, root=ROOT)
    pair_provider = FakeProvider(_pair_payload(manifest.document_id))
    first = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path, provider=pair_provider,
        allow_api=True, confirmed=True, root=ROOT,
    )
    second = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path, provider=pair_provider,
        allow_api=True, confirmed=True, root=ROOT,
    )
    assert first.api_calls_started == 1
    assert second.api_calls_started == 0
    assert second.cache_hits == 1
    assert pair_provider.calls == 1
    assert first.triple_calls == 0
    assert first.retries == 0


def test_pair_string_confidence_is_compatibly_normalized(prepared) -> None:
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    payload = _pair_payload(manifest.document_id)
    payload["confidence"] = "high"
    provider = FakeProvider(payload)
    result = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path, provider=provider,
        allow_api=True, confirmed=True, root=ROOT,
    )
    normalized = load_json(result.normalized_output_path)
    assert normalized["confidence_raw"] == "high"
    assert normalized["confidence_label"] == "high"
    assert normalized["confidence_score"] is None
    assert any(event["action"] == "string_label_preserved_without_numeric_score" for event in normalized["normalization_events"])
    assert provider.calls == 1
    assert result.status == "completed"
    preflight = phase3b_preflight(settings, manifest=manifest, root=ROOT)
    assert preflight.calls_already_started == 2
    assert preflight.remaining_real_calls == 0
    assert preflight.ready is True


def test_offline_pair_recovery_preserves_raw_usage_and_ledger(prepared) -> None:
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    payload = _pair_payload(manifest.document_id)
    payload["confidence"] = "high"
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path, provider=FakeProvider(payload),
        allow_api=True, confirmed=True, root=ROOT,
    )
    pair_directory = Path(pair.raw_response_path).parent
    normalized_path = Path(pair.normalized_output_path)
    cache_path = pair_directory / "cache.json"
    protected = {
        "raw": (sha256_file(pair.raw_response_path), Path(pair.raw_response_path).stat().st_mtime_ns),
        "usage": (sha256_file(pair.usage_path), Path(pair.usage_path).stat().st_mtime_ns),
        "ledger": (
            sha256_file(Path(settings.phase3b_data_directory) / "phase3b_call_ledger.json"),
            (Path(settings.phase3b_data_directory) / "phase3b_call_ledger.json").stat().st_mtime_ns,
        ),
    }
    normalized_path.unlink()
    cache_path.unlink()

    recovered = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path,
        allow_api=False, confirmed=False, root=ROOT,
    )

    assert recovered.api_calls_started == 0
    assert recovered.cache_hits == 1
    assert load_json(recovered.normalized_output_path)["confidence_score"] is None
    current = {
        "raw": (sha256_file(pair.raw_response_path), Path(pair.raw_response_path).stat().st_mtime_ns),
        "usage": (sha256_file(pair.usage_path), Path(pair.usage_path).stat().st_mtime_ns),
        "ledger": (
            sha256_file(Path(settings.phase3b_data_directory) / "phase3b_call_ledger.json"),
            (Path(settings.phase3b_data_directory) / "phase3b_call_ledger.json").stat().st_mtime_ns,
        ),
    }
    assert current == protected


def test_python_boundary_parser_inserts_space_and_closes_boundary() -> None:
    payload = _pair_payload("doc_test")
    payload["confidence"] = "high"
    payload["visible_trailing_hyphen"] = True
    payload["suspected_word_continuation"] = True
    observation = Phase3BBoundaryObservation.model_validate(payload)
    decision = resolve_phase3b_boundary(
        previous_text="who had obviously been doing",
        next_text="King George the fullest justice",
        observation=observation,
        text_layer_tail="who had obviously been doing",
        text_layer_head="King George the fullest justice",
    )
    assert decision.join_operation == "insert_space"
    assert decision.resolved_text == "who had obviously been doing King George the fullest justice"
    assert "doingKing" not in decision.resolved_text
    assert decision.auto_resolution_status == "resolved_pair"
    assert decision.observed_word_continuation is True
    assert decision.validated_word_continuation is False
    assert decision.word_continuation is False
    assert decision.joiner == " "
    assert decision.sentence_continuation is True
    assert decision.paragraph_continuation is True
    assert any("doingKing" in item for item in decision.conflicting_evidence)


def test_no_visible_tail_hyphen_forbids_no_space_join() -> None:
    payload = _pair_payload("doc_test")
    payload["visible_trailing_hyphen"] = True
    payload["suspected_word_continuation"] = True
    observation = Phase3BBoundaryObservation.model_validate(payload)
    decision = resolve_phase3b_boundary(
        previous_text="doing",
        next_text="King",
        observation=observation,
        text_layer_tail="doing",
        text_layer_head="King",
    )
    assert decision.join_operation == "insert_space"
    assert decision.joiner == " "
    assert decision.resolved_text == "doing King"
    assert decision.validated_word_continuation is False


def test_reconstruction_closes_11_12_and_has_no_12_13(prepared) -> None:
    settings, manifest = prepared
    page_provider = FakeProvider(_page_payload(manifest.document_id))
    page = run_phase3b_page12(settings, manifest, provider=page_provider, allow_api=True, confirmed=True, root=ROOT)
    pair_provider = FakeProvider(_pair_payload(manifest.document_id))
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path, provider=pair_provider,
        allow_api=True, confirmed=True, root=ROOT,
    )
    document = build_phase3b_source_document(
        settings, manifest, page.normalized_output_path, pair.normalized_output_path, root=ROOT
    )
    assert document.audit.boundary_11_12_closed is True
    assert document.audit.open_boundary_12_13_exists is False
    assert document.entries[-1].sentence_complete is True
    assert document.entries[-1].paragraph_complete is True
    assert document.entries[-1].completeness_status == "complete"
    assert document.entries[-1].unresolved_boundaries == []


def test_all_fragments_are_used_exactly_once(prepared) -> None:
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path,
        provider=FakeProvider(_pair_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    document = build_phase3b_source_document(
        settings, manifest, page.normalized_output_path, pair.normalized_output_path, root=ROOT
    )
    assert document.audit.unused_fragment_ids == []
    assert document.audit.duplicate_fragment_ids == []
    assert document.audit.expected_fragment_count == document.audit.referenced_fragment_count


def test_source_document_excludes_headers_page_numbers_and_chinese_fields(prepared) -> None:
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path,
        provider=FakeProvider(_pair_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    document = build_phase3b_source_document(
        settings, manifest, page.normalized_output_path, pair.normalized_output_path, root=ROOT
    )
    encoded = document.model_dump_json()
    source = "\n".join(entry.source_text for entry in document.entries)
    assert "chinese_text" not in encoded
    assert "translation_text" not in encoded.casefold()
    assert "chinese_translation" not in encoded.casefold()
    assert "Univ Calif - Digitized by Microsoft" not in source
    assert "\n12\n" not in source
    assert "SHANGHAI\nSHANGHAI" not in source


def test_markdown_and_docx_are_exported_from_same_json(prepared) -> None:
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path,
        provider=FakeProvider(_pair_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    document = build_phase3b_source_document(
        settings, manifest, page.normalized_output_path, pair.normalized_output_path, root=ROOT
    )
    result = export_phase3b_source(settings, master_path=settings.phase3b_master_path, root=ROOT)
    assert result.strict_exported is True
    assert Path(result.diagnostic_markdown_path).is_file()
    assert Path(result.diagnostic_docx_path).is_file()
    assert Path(result.final_markdown_path).is_file()
    assert Path(result.final_docx_path).is_file()
    markdown = Path(result.final_markdown_path).read_text(encoding="utf-8")
    word_text = "\n".join(p.text for p in Document(result.final_docx_path).paragraphs)
    for entry in document.entries:
        assert entry.logical_block_id in markdown
        assert entry.logical_block_id in word_text
        assert entry.source_text in markdown
        assert entry.source_text in word_text
    assert result.source_json_sha256 == sha256_file(settings.phase3b_master_path)


def test_strict_export_is_blocked_when_master_is_unresolved(prepared) -> None:
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    bad_pair = _pair_payload(manifest.document_id)
    bad_pair["suspected_sentence_continuation"] = None
    bad_pair["status"] = "uncertain"
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path, provider=FakeProvider(bad_pair),
        allow_api=True, confirmed=True, root=ROOT,
    )
    document = build_phase3b_source_document(
        settings, manifest, page.normalized_output_path, pair.normalized_output_path, root=ROOT
    )
    assert document.strict_export_ready is False
    result = export_phase3b_source(settings, master_path=settings.phase3b_master_path, root=ROOT)
    assert result.strict_exported is False
    assert result.final_markdown_path is None
    assert result.final_docx_path is None


def test_phase3b_does_not_modify_phase3a_or_bilingual_master(prepared) -> None:
    before_master = sha256_file(ROOT / "data/bilingual_document.json")
    before_phase3a = sha256_file(ROOT / "data/phase3a_translation_sample.json")
    settings, manifest = prepared
    page = run_phase3b_page12(
        settings, manifest, provider=FakeProvider(_page_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    pair = run_phase3b_pair_11_12(
        settings, manifest, page.normalized_output_path,
        provider=FakeProvider(_pair_payload(manifest.document_id)),
        allow_api=True, confirmed=True, root=ROOT,
    )
    build_phase3b_source_document(
        settings, manifest, page.normalized_output_path, pair.normalized_output_path, root=ROOT
    )
    assert sha256_file(ROOT / "data/bilingual_document.json") == before_master
    assert sha256_file(ROOT / "data/phase3a_translation_sample.json") == before_phase3a
