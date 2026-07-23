from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from bookflow.fullbook_pipeline import (
    FullbookCheckpointStore,
    build_fullbook_preflight,
    build_fullbook_automated_pages,
    build_fullbook_translation_units,
    import_matching_sample_vision_cache,
    partition_pages,
    recover_fullbook_vision_normalization,
    render_fullbook_pages,
    run_fullbook_vision_batch,
    run_fullbook_pair_batch,
    run_fullbook_translation_batch,
    select_fullbook_pair_candidates,
)
from bookflow.io_utils import load_json, sha256_file
from bookflow.paths import load_settings


def _pdf(path: Path, pages: int) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=300 + number, height=500 + number)
        page.insert_text((40, 60), f"Page {number}")
    document.save(path)
    document.close()
    return path


def test_preflight_reads_dynamic_pdf_page_count_and_boundary_count(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "book (test).pdf", 3)
    settings = load_settings()

    report = build_fullbook_preflight(settings, root=tmp_path, source_pdf=source)

    assert report.actual_page_count == 3
    assert report.potential_boundary_count == 2
    assert report.source_pdf_sha256 == sha256_file(source)


def test_preflight_does_not_require_412_pages(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "short.pdf", 2)
    report = build_fullbook_preflight(load_settings(), root=tmp_path, source_pdf=source)

    assert report.actual_page_count == 2
    assert report.ready is True


def test_dynamic_batch_partition_uses_configured_size() -> None:
    assert partition_pages(5, 2) == [[1, 2], [3, 4], [5]]
    assert partition_pages(1, 20) == [[1]]


def test_checkpoint_round_trip_and_resume(tmp_path: Path) -> None:
    store = FullbookCheckpointStore(tmp_path / "checkpoint.json", source_pdf_sha256="abc")
    store.mark_completed("render", "page_0001")

    resumed = FullbookCheckpointStore(tmp_path / "checkpoint.json", source_pdf_sha256="abc")

    assert resumed.is_completed("render", "page_0001") is True
    assert resumed.is_completed("render", "page_0002") is False


def test_checkpoint_refuses_changed_source_pdf_hash(tmp_path: Path) -> None:
    store = FullbookCheckpointStore(tmp_path / "checkpoint.json", source_pdf_sha256="abc")
    store.mark_completed("render", "page_0001")

    with pytest.raises(RuntimeError, match="source PDF hash changed"):
        FullbookCheckpointStore(tmp_path / "checkpoint.json", source_pdf_sha256="changed")


def test_fullbook_paths_are_isolated_from_sample_outputs(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "book.pdf", 2)
    report = build_fullbook_preflight(load_settings(), root=tmp_path, source_pdf=source)

    assert "data\\fullbook" in report.checkpoint_path or "data/fullbook" in report.checkpoint_path
    assert "output\\fullbook" in report.candidate_directory or "output/fullbook" in report.candidate_directory
    assert "sample12" not in report.checkpoint_path


def test_invalid_zero_page_pdf_stops_preflight(tmp_path: Path) -> None:
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF")

    with pytest.raises(Exception):
        build_fullbook_preflight(load_settings(), root=tmp_path, source_pdf=source)


def test_fullbook_render_is_isolated_and_resumable(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "book.pdf", 3)
    settings = load_settings().model_copy(update={"source_pdf": str(source)})
    source_hash = sha256_file(source)

    first = render_fullbook_pages(settings, root=tmp_path, source_pdf=source, pages=[1, 2, 3])
    second = render_fullbook_pages(settings, root=tmp_path, source_pdf=source, pages=[1, 2, 3])

    assert first.rendered_pages == [1, 2, 3]
    assert second.cached_pages == [1, 2, 3]
    assert sha256_file(source) == source_hash
    assert all("data\\fullbook" in path or "data/fullbook" in path for path in first.page_record_paths)


def test_matching_sample_vision_cache_is_rebound_without_api(tmp_path: Path) -> None:
    """An identical rendered image may reuse transcription while keeping provenance."""
    from bookflow.io_utils import atomic_write_json

    settings = load_settings()
    prompt = tmp_path / "prompts" / "vision_transcription_v2.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("test prompt", encoding="utf-8")
    fullbook = tmp_path / "data" / "fullbook"
    record_dir = fullbook / "page_manifests" / "book" / "profile" / "pages"
    image_dir = fullbook / "pages" / "book" / "profile"
    image_dir.mkdir(parents=True)
    record_dir.mkdir(parents=True)
    image = image_dir / "page_0005.png"
    image.write_bytes(b"same-rendered-image")
    image_hash = sha256_file(image)
    atomic_write_json(record_dir / "page_0005.json", {
        "document_id": "doc_full", "pdf_page": 5, "image_path": str(image),
        "image_sha256": image_hash, "source_pdf_sha256": "fullhash",
    })

    raw = tmp_path / "sample_raw.json"
    normalized = tmp_path / "sample_normalized.json"
    atomic_write_json(raw, {"api_called": True, "response": {"choices": [{"message": {"content": "{}"}}]}})
    atomic_write_json(normalized, {
        "schema_version": "1.1", "source_model_schema_version": "2.0",
        "document_id": "doc_sample", "pdf_page": 1, "provider": "zhipu",
        "model": "glm-4.6v", "page_type": "body", "printed_page": None,
        "title": None, "running_header": None, "footer": None,
        "page_number_text": None, "blocks": [],
        "continuation_from_previous": None, "continuation_to_next": None,
        "boundary_status": "needs_adjacent_context", "boundary_review_required": True,
        "boundary_notes": None, "uncertain_characters": [], "warnings": [],
        "normalization_events": [], "raw_response_path": str(raw),
        "previous_normalized_output_path": None, "raw_response_sha256": sha256_file(raw),
        "previous_normalized_sha256": None, "normalized_at": "2026-01-01T00:00:00Z",
        "status": "needs_review", "authoritative": False, "api_called": True,
        "translation_ready": False,
    })
    sample_manifest = tmp_path / "sample_manifest.json"
    atomic_write_json(sample_manifest, {"pages": [{
        "pdf_page": 1, "image_sha256": image_hash,
        "normalized_visual_path": str(normalized),
    }]})

    result = import_matching_sample_vision_cache(
        settings, root=tmp_path, sample_manifests=[sample_manifest]
    )

    assert result.imported_pages == [5]
    assert result.api_calls_this_run == 0
    rebound = load_json(result.normalized_paths[0])
    assert rebound["document_id"] == "doc_full"
    assert rebound["pdf_page"] == 5
    assert rebound["api_called"] is False
    assert any(event["action"] == "reused_identical_rendered_image" for event in rebound["normalization_events"])
    assert sha256_file(raw) == load_json(normalized)["raw_response_sha256"]


def test_nonmatching_image_hash_is_not_imported(tmp_path: Path) -> None:
    from bookflow.io_utils import atomic_write_json

    settings = load_settings()
    record_dir = tmp_path / "data" / "fullbook" / "page_manifests" / "book" / "profile" / "pages"
    record_dir.mkdir(parents=True)
    atomic_write_json(record_dir / "page_0001.json", {
        "document_id": "doc_full", "pdf_page": 1, "image_path": "unused.png",
        "image_sha256": "full-image", "source_pdf_sha256": "fullhash",
    })
    manifest = tmp_path / "sample_manifest.json"
    atomic_write_json(manifest, {"pages": [{
        "pdf_page": 1, "image_sha256": "different-image",
        "normalized_visual_path": str(tmp_path / "missing.json"),
    }]})

    result = import_matching_sample_vision_cache(settings, root=tmp_path, sample_manifests=[manifest])

    assert result.imported_pages == []
    assert result.api_calls_this_run == 0


class _FakeVisionProvider:
    calls = 0

    def __init__(self, **_: object) -> None:
        pass

    def transcribe_one_page(self, **kwargs: object):
        import json
        from bookflow.vision_provider import ProviderResponse

        type(self).calls += 1
        context = str(kwargs["context_message"])
        document_id = context.split("document_id=", 1)[1].split(";", 1)[0]
        page = int(context.split("pdf_page=", 1)[1].split(";", 1)[0])
        payload = {
            "schema_version": "2.0", "document_id": document_id, "pdf_page": page,
            "provider": "zhipu", "model": "glm-4.6v", "page_type": "body",
            "printed_page": str(page), "title": None, "running_header": None,
            "footer": None, "page_number_text": str(page),
            "blocks": [{"block_id": f"p{page}_b1", "block_type": "body", "order": 1,
                        "text": f"Visible page {page}.", "bounding_box": None,
                        "confidence": None, "uncertain": False, "notes": None}],
            "continuation_from_previous": None, "continuation_to_next": None,
            "boundary_notes": "Adjacent context required.", "uncertain_characters": [],
            "warnings": [], "status": "technical_validation_only", "translation_ready": False,
        }
        raw = {"id": f"r{page}", "model": "glm-4.6v", "choices": [{"message": {"content": json.dumps(payload)}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        return ProviderResponse(raw_response=raw, content=json.dumps(payload), request_id=f"r{page}",
                                usage=raw["usage"], response_model="glm-4.6v")


class _FailingVisionProvider:
    def __init__(self, **_: object) -> None:
        pass

    def transcribe_one_page(self, **_: object):
        raise TimeoutError("simulated timeout")


class _FakePairProvider:
    calls = 0

    def __init__(self, **_: object) -> None:
        pass

    def transcribe_images(self, **kwargs: object):
        import json
        from bookflow.vision_provider import ProviderResponse

        type(self).calls += 1
        context = json.loads(str(kwargs["context_message"]))
        payload = {
            "schema_version": "1.0", "boundary_id": context["boundary_id"],
            "document_id": context["document_id"], "previous_page": context["previous_page"],
            "next_page": context["next_page"], "previous_last_block_id": None,
            "next_first_block_id": None, "word_continuation": False,
            "sentence_continuation": True, "paragraph_continuation": True,
            "structural_break": "none", "join_operation": "concatenate_with_space",
            "hyphen_type": "no_hyphen", "header_footer_interference": False,
            "reconstructed_boundary_text": "Visible page 1. Visible page 2.",
            "evidence": ["two visible complete words"], "confidence": 0.9,
            "needs_triple_review": False, "needs_human_review": False, "status": "reviewed",
        }
        content = json.dumps(payload)
        raw = {"id": "pair1", "model": "glm-4.6v", "choices": [{"message": {"content": content}}],
               "usage": {"total_tokens": 30}}
        return ProviderResponse(raw_response=raw, content=content, request_id="pair1",
                                usage=raw["usage"], response_model="glm-4.6v")


class _FakeTranslationProvider:
    calls = 0

    def __init__(self, **_: object) -> None:
        pass

    def translate_one(self, **kwargs: object):
        import json
        from bookflow.translation_provider import TranslationProviderResponse

        type(self).calls += 1
        unit = kwargs["user_payload"]
        payload = {
            "target_block_id": unit["target_block_id"], "block_type": unit["block_type"],
            "translation": "这是一个完整的测试译文。", "untranslated_source_terms": [],
            "warnings": [],
        }
        content = json.dumps(payload, ensure_ascii=False)
        raw = {"id": f"t{type(self).calls}", "model": "deepseek-v4-pro",
               "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 40}}
        return TranslationProviderResponse(raw_response=raw, content=content,
                                           request_id=raw["id"], usage=raw["usage"],
                                           response_model="deepseek-v4-pro")


def _vision_fixture(tmp_path: Path, pages: int = 3):
    from bookflow.io_utils import atomic_write_json

    prompt = tmp_path / "prompts" / "vision_transcription_v2.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("test prompt", encoding="utf-8")
    records = tmp_path / "data" / "fullbook" / "page_manifests" / "book" / "profile" / "pages"
    images = tmp_path / "data" / "fullbook" / "pages" / "book" / "profile"
    records.mkdir(parents=True)
    images.mkdir(parents=True)
    for page in range(1, pages + 1):
        image = images / f"page_{page:04d}.png"
        image.write_bytes(f"image-{page}".encode())
        atomic_write_json(records / f"page_{page:04d}.json", {
            "document_id": "doc_full", "pdf_page": page, "image_path": str(image),
            "image_sha256": sha256_file(image), "source_pdf_sha256": "fullhash",
        })
    settings = load_settings().model_copy(update={"vision_api_enabled": True})
    return settings


def test_fullbook_vision_saves_each_page_and_replays_from_cache(tmp_path: Path) -> None:
    _FakeVisionProvider.calls = 0
    settings = _vision_fixture(tmp_path, 2)
    first = run_fullbook_vision_batch(
        settings, pages=[1, 2], root=tmp_path, allow_api=True,
        provider_factory=_FakeVisionProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    second = run_fullbook_vision_batch(
        settings, pages=[1, 2], root=tmp_path, allow_api=True,
        provider_factory=_FakeVisionProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )

    assert first.api_calls_this_run == 2
    assert second.api_calls_this_run == 0
    assert second.cached_pages == [1, 2]
    assert _FakeVisionProvider.calls == 2
    assert all(Path(path).is_file() for path in first.raw_paths + first.normalized_paths)
    assert set(first.raw_paths).isdisjoint(first.normalized_paths)


def test_three_consecutive_same_vision_failures_stop_run(tmp_path: Path) -> None:
    settings = _vision_fixture(tmp_path, 4)

    with pytest.raises(RuntimeError, match="three consecutive"):
        run_fullbook_vision_batch(
            settings, pages=[1, 2, 3, 4], root=tmp_path, allow_api=True,
            provider_factory=_FailingVisionProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
        )

    ledger = load_json(tmp_path / "data" / "fullbook" / "vision" / "call_ledger.json")
    assert ledger["real_calls_started"] == 3
    assert len(ledger["attempts"]) == 3
    assert all(item["automatic_retry"] is False for item in ledger["attempts"])


def test_saved_raw_response_is_recovered_offline_without_new_call(tmp_path: Path) -> None:
    import json
    from bookflow.io_utils import atomic_write_json

    settings = _vision_fixture(tmp_path, 1)
    record = load_json(next((tmp_path / "data/fullbook/page_manifests").rglob("page_0001.json")))
    from bookflow.fullbook_pipeline import _transcription_identity
    fingerprint = _transcription_identity(settings, record["image_sha256"], tmp_path)
    vision = tmp_path / "data/fullbook/vision"
    payload = {
        "schema_version": "2.0", "document_id": "doc_full", "pdf_page": 1,
        "provider": "zhipu", "model": "glm-4.6v", "page_type": "front_matter",
        "printed_page": None, "title": None, "running_header": None, "footer": None,
        "page_number_text": None,
        "blocks": [{"block_id": "b1", "block_type": "title", "order": 1,
                    "text": "BOOK TITLE", "bounding_box": None, "confidence": "high",
                    "uncertain": False, "notes": None}],
        "continuation_from_previous": False, "continuation_to_next": False,
        "boundary_notes": "Complete.", "uncertain_characters": [], "warnings": [],
        "status": "technical_validation_only", "translation_ready": False,
    }
    raw = vision / "raw" / f"{fingerprint}.json"
    atomic_write_json(raw, {"api_called": True, "response": {
        "id": "r1", "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"total_tokens": 25},
    }})
    atomic_write_json(vision / "errors" / f"page_0001_{fingerprint}.json", {
        "pdf_page": 1, "request_fingerprint": fingerprint, "error_type": "ValidationError",
    })
    atomic_write_json(vision / "call_ledger.json", {
        "schema_version": "fullbook-vision-ledger-1.0", "source_pdf_sha256": "fullhash",
        "real_calls_started": 1, "attempts": [{"pdf_page": 1, "request_fingerprint": fingerprint,
        "status": "failed", "automatic_retry": False}],
    })

    result = recover_fullbook_vision_normalization(settings, root=tmp_path)

    assert result.recovered_pages == [1]
    assert result.api_calls_this_run == 0
    assert load_json(vision / "call_ledger.json")["real_calls_started"] == 1
    cache = load_json(vision / "cache" / "page_0001.json")
    normalized = load_json(cache["normalized_output_path"])
    assert normalized["blocks"][0]["block_type"] == "unknown"
    assert normalized["blocks"][0]["confidence"] is None


def test_pair_candidates_include_only_unresolved_internal_boundaries() -> None:
    from datetime import datetime, timezone
    from bookflow.phase2b2_schemas import AutomatedBoundary

    def boundary(page: int, status: str) -> AutomatedBoundary:
        return AutomatedBoundary(
            schema_version="2.0", boundary_id=f"boundary_p{page:04d}_p{page+1:04d}",
            document_id="doc", previous_page=page, next_page=page + 1,
            previous_fragment_id=f"l{page}", next_fragment_id=f"r{page}",
            previous_tail_text="left", next_head_text="right",
            word_continuation=None if status == "unresolved" else False,
            sentence_continuation=None if status == "unresolved" else False,
            paragraph_continuation=None if status == "unresolved" else False,
            structural_break="unknown" if status == "unresolved" else "paragraph_break",
            join_operation="unresolved" if status == "unresolved" else "no_join",
            visible_trailing_hyphen=False, resolution_method="test",
            supporting_evidence=[], conflicting_evidence=[], resolution_reason="test",
            auto_resolution_status=status, source_inputs=[], created_at=datetime.now(timezone.utc),
        )

    missing = boundary(4, "unresolved").model_copy(
        update={"previous_fragment_id": "fragment_missing_deadbeef"}
    )
    candidates = select_fullbook_pair_candidates([
        boundary(1, "resolved_primary"), boundary(2, "unresolved"),
        boundary(3, "resolved_pair"), missing,
    ])

    assert [item.boundary_id for item in candidates] == ["boundary_p0002_p0003"]


def test_fullbook_pair_runner_is_cached_and_never_translates(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from bookflow.phase2b2_schemas import AutomatedBoundary

    _FakeVisionProvider.calls = 0
    _FakePairProvider.calls = 0
    settings = _vision_fixture(tmp_path, 2)
    boundary_prompt = tmp_path / "prompts" / "boundary_review_v1.md"
    boundary_prompt.write_text("boundary test prompt", encoding="utf-8")
    run_fullbook_vision_batch(
        settings, pages=[1, 2], root=tmp_path, allow_api=True,
        provider_factory=_FakeVisionProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    candidate = AutomatedBoundary(
        schema_version="2.0", boundary_id="boundary_p0001_p0002", document_id="doc_full",
        previous_page=1, next_page=2, previous_fragment_id="left", next_fragment_id="right",
        previous_tail_text="Visible page 1", next_head_text="Visible page 2",
        word_continuation=None, sentence_continuation=None, paragraph_continuation=None,
        structural_break="unknown", join_operation="unresolved", visible_trailing_hyphen=False,
        resolution_method="primary", supporting_evidence=[], conflicting_evidence=[],
        resolution_reason="uncertain", auto_resolution_status="unresolved", source_inputs=[],
        created_at=datetime.now(timezone.utc),
    )

    first = run_fullbook_pair_batch(
        settings, [candidate], root=tmp_path, allow_api=True,
        provider_factory=_FakePairProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    second = run_fullbook_pair_batch(
        settings, [candidate], root=tmp_path, allow_api=True,
        provider_factory=_FakePairProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )

    assert first.api_calls_this_run == 1
    assert second.api_calls_this_run == 0
    assert second.cached_boundaries == [candidate.boundary_id]
    assert _FakePairProvider.calls == 1
    saved = load_json(first.normalized_paths[0])
    assert saved["translation_blocked_reason"] is None
    assert "translation" not in load_json(
        tmp_path / "data/fullbook/boundaries/boundary_call_ledger.json"
    ).get("api_calls", {})


def test_fullbook_page_adapter_preserves_fragments_and_dynamic_count(tmp_path: Path) -> None:
    from bookflow.io_utils import atomic_write_json

    source = tmp_path / "book.pdf"
    document = fitz.open()
    for text_value in ("They were doing", "King's work."):
        page = document.new_page()
        page.insert_text((50, 60), text_value)
    document.save(source)
    document.close()
    settings = load_settings().model_copy(update={"source_pdf": str(source)})
    records = tmp_path / "data/fullbook/page_manifests/book/profile/pages"
    images = tmp_path / "data/fullbook/pages/book/profile"
    records.mkdir(parents=True)
    images.mkdir(parents=True)
    for page, text_value in enumerate(("They were doing", "King's work."), 1):
        image = images / f"page_{page:04d}.png"
        image.write_bytes(f"image{page}".encode())
        atomic_write_json(records / f"page_{page:04d}.json", {
            "document_id": "doc_full", "pdf_page": page, "image_path": str(image),
            "image_sha256": sha256_file(image), "source_pdf_sha256": sha256_file(source),
        })
        normalized_path = tmp_path / f"normalized_{page}.json"
        atomic_write_json(normalized_path, {
            "schema_version": "1.1", "source_model_schema_version": "2.0",
            "document_id": "doc_full", "pdf_page": page, "provider": "zhipu",
            "model": "glm-4.6v", "page_type": "body", "printed_page": str(page),
            "title": None, "running_header": None, "footer": None, "page_number_text": None,
            "blocks": [{"block_id": f"b{page}", "block_type": "body", "order": 1,
                        "text": text_value, "bounding_box": None, "confidence": None,
                        "uncertain": False, "notes": None}],
            "continuation_from_previous": None, "continuation_to_next": None,
            "boundary_status": "needs_adjacent_context", "boundary_review_required": True,
            "boundary_notes": None, "uncertain_characters": [], "warnings": [],
            "normalization_events": [], "raw_response_path": str(tmp_path / f"raw{page}.json"),
            "previous_normalized_output_path": None, "raw_response_sha256": "0" * 64,
            "previous_normalized_sha256": None, "normalized_at": "2026-01-01T00:00:00Z",
            "status": "needs_review", "authoritative": False, "api_called": True,
            "translation_ready": False,
        })
        raw = tmp_path / f"raw{page}.json"
        raw.write_text("{}", encoding="utf-8")
        atomic_write_json(tmp_path / f"data/fullbook/vision/cache/page_{page:04d}.json", {
            "status": "completed", "normalized_output_path": str(normalized_path),
            "raw_response_path": str(raw),
        })
    preflight = build_fullbook_preflight(settings, root=tmp_path, source_pdf=source)

    result = build_fullbook_automated_pages(settings, preflight, root=tmp_path)

    assert result.page_count == 2
    assert result.fragment_count == 2
    pages = [json.loads(line) for line in Path(result.output_path).read_text(encoding="utf-8").splitlines()]
    assert pages[0]["tail_fragment"]["text"] == "They were doing"
    assert pages[1]["head_fragment"]["text"] == "King's work."

    (tmp_path / "data/fullbook/vision/cache/page_0002.json").unlink()
    missing_result = build_fullbook_automated_pages(settings, preflight, root=tmp_path)
    missing_pages = [
        json.loads(line) for line in Path(missing_result.output_path).read_text(encoding="utf-8").splitlines()
    ]
    assert missing_pages[1]["source_coverage_status"] == "failed"
    assert missing_pages[1]["transcription_status"] == "failed_missing_visual_transcription"
    assert missing_pages[1]["content_fragments"][0]["text"] == ""
    assert missing_pages[1]["content_fragments"][0]["uncertainty"] == ["missing_visual_transcription"]


def test_fullbook_translation_units_translate_titles_but_keep_body_context_separate() -> None:
    from datetime import datetime, timezone
    from bookflow.phase2b2_schemas import AutomatedLogicalBlock

    def logical(block_id: str, block_type: str, text_value: str, chapter: str | None):
        return AutomatedLogicalBlock(
            schema_version="2.0", logical_block_id=block_id, document_id="doc",
            source_pages=[1], source_fragment_ids=[f"f_{block_id}"],
            source_block_ids=[f"b_{block_id}"], source_text=text_value,
            block_type=block_type, chapter_id=chapter, section_id=None, cross_page=False,
            sentence_complete=True, paragraph_complete=True, coverage_complete=True,
            unresolved_boundaries=[], header_footer_page_number_clean=True,
            translation_ready=True, created_at=datetime.now(timezone.utc),
        )

    blocks = [
        logical("chapter", "chapter_title", "CHAPTER I", "chapter"),
        logical("body1", "body", "First paragraph.", "chapter"),
        logical("body2", "body", "Second paragraph.", "chapter"),
    ]

    units = build_fullbook_translation_units(blocks)

    by_id = {item.target_block_id: item for item in units}
    assert by_id["chapter"].source_text == "CHAPTER I"
    assert by_id["chapter"].block_type == "chapter_title"
    assert by_id["body1"].chapter_title_context == "CHAPTER I"
    assert by_id["body1"].source_text == "First paragraph."
    assert by_id["body1"].translate_target_only is True
    assert by_id["body1"].context_after_text == "Second paragraph."


def test_fullbook_translation_batch_is_cached_and_target_only(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from bookflow.phase2b2_schemas import AutomatedLogicalBlock

    prompt = tmp_path / "prompts/translation_en_zh_v2.md"
    profile = tmp_path / "language_profiles/zh-Hans.yaml"
    prompt.parent.mkdir(parents=True)
    profile.parent.mkdir(parents=True)
    prompt.write_text("Translate only source_text and return JSON.", encoding="utf-8")
    profile.write_text("style: faithful\n", encoding="utf-8")
    block = AutomatedLogicalBlock(
        schema_version="2.0", logical_block_id="body1", document_id="doc",
        source_pages=[1], source_fragment_ids=["f1"], source_block_ids=["b1"],
        source_text="A complete paragraph for translation.", block_type="body",
        chapter_id=None, section_id=None, cross_page=False, sentence_complete=True,
        paragraph_complete=True, coverage_complete=True, unresolved_boundaries=[],
        header_footer_page_number_clean=True, translation_ready=True,
        created_at=datetime.now(timezone.utc),
    )
    settings = load_settings()
    units = build_fullbook_translation_units([block])
    _FakeTranslationProvider.calls = 0

    first = run_fullbook_translation_batch(
        settings, units, root=tmp_path, allow_api=True,
        provider_factory=_FakeTranslationProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    second = run_fullbook_translation_batch(
        settings, units, root=tmp_path, allow_api=True,
        provider_factory=_FakeTranslationProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )

    assert first.api_calls_this_run == 1
    assert second.api_calls_this_run == 0
    assert second.cache_hits == 1
    assert _FakeTranslationProvider.calls == 1
    assert first.results[0].target_block_id == "body1"
    request = load_json(first.request_paths[0])
    assert request["payload"]["source_text"] == block.source_text
    assert request["payload"]["translate_target_only"] is True
