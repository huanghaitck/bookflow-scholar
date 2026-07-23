from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from bookflow.io_utils import atomic_write_json, load_json
from bookflow.page_pipeline import render_pages
from bookflow.paths import ProjectSettings, project_root
from bookflow.phase2a1 import normalize_preserved_response_v11
from bookflow.phase2b_calls import (
    create_open_boundary_11_12,
    phase2b_preflight,
    run_pair_boundaries,
    run_single_pages,
    select_triple_candidates,
)
from bookflow.phase2b_qa import run_offline_boundary_qa
from bookflow.phase2b_schemas import BoundaryDecision, LogicalBlock, TranslationUnit
from bookflow.reconstruction import build_logical_blocks, validate_logical_outputs
from bookflow.vision_provider import ProviderResponse


ROOT = project_root()
SAMPLE = ROOT / "input" / "sample_11_pages.pdf"
FULL = ROOT / "input" / "The big game of central and western China (1913).pdf"
KEY_ENV = "BOOKFLOW_PHASE2B_TEST_KEY"


def _settings(root: Path) -> ProjectSettings:
    return ProjectSettings.model_validate(
        {
            "source_pdf": str(FULL),
            "sample_pdf": str(SAMPLE),
            "output_directory": str(root / "output"),
            "cache_directory": str(root / "cache"),
            "render_dpi": 72,
            "page_image_directory": str(root / "pages"),
            "manifest_directory": str(root / "manifests"),
            "mock_vision_directory": str(root / "mock"),
            "continuity_directory": str(root / "continuity"),
            "log_directory": str(root / "logs"),
            "temporary_directory": str(root / "tmp"),
            "vision_provider": "mock-zhipu",
            "vision_base_url": "https://example.invalid/v4",
            "vision_model": "mock-glm",
            "vision_api_key_env": KEY_ENV,
            "vision_compatible_api_key_envs": [],
            "vision_prompt_path": str(ROOT / "prompts/vision_transcription_v1.md"),
            "vision_normalized_v11_directory": str(root / "vision_v11"),
            "phase2b_page_prompt_path": str(ROOT / "prompts/vision_transcription_v2.md"),
            "boundary_prompt_path": str(ROOT / "prompts/boundary_review_v1.md"),
            "vision_raw_directory": str(root / "vision_raw"),
            "vision_request_directory": str(root / "vision_requests"),
            "vision_usage_directory": str(root / "vision_usage"),
            "vision_cache_directory": str(root / "vision_cache"),
            "phase2b_page_cache_directory": str(root / "phase2b_page_cache"),
            "phase2b_request_directory": str(root / "phase2b_requests"),
            "phase2b_usage_directory": str(root / "phase2b_usage"),
            "boundary_raw_directory": str(root / "boundary_raw"),
            "boundary_normalized_directory": str(root / "boundary_normalized"),
            "boundary_cache_directory": str(root / "boundary_cache"),
            "boundary_review_directory": str(root / "boundary_reviews"),
            "logical_block_directory": str(root / "logical"),
            "logical_manifest_directory": str(root / "logical_manifests"),
            "translation_context_directory": str(root / "translation_context"),
            "human_review_directory": str(root / "review"),
            "boundary_qa_reference_path": str(ROOT / "config/sample_boundary_qa_reference.yaml"),
            "boundary_qa_output_path": str(root / "review/sample_boundary_qa.json"),
            "translation_provider": "disabled",
            "translation_base_url": "https://translation.invalid/v1",
            "translation_model": "disabled",
            "maximum_cash_cost_cny": 2.0,
            "default_page_range": [1, 11],
            "sample_page_range": [1, 11],
            "dry_run": True,
        }
    )


def _blocks(page: int) -> list[dict]:
    texts = {
        1: ["Page one unfinished"],
        2: ["continues one. Page two ends."],
        3: ["The voyage starts in a complete paragraph."],
        4: ["A separate complete paragraph on page four."],
        5: ["Her luggage could not arrive before the"],
        6: ["boat sailed and the first paragraph ends.", "Middle complete.", "and there is much"],
        7: ["of similarity and that paragraph ends.", "A long paragraph begins"],
        8: ["and ends on page eight."],
        9: ["CHAPTER II", "Chapter two begins and continues"],
        10: ["through page ten and continues"],
        11: ["into page eleven but remains unfinished"],
    }[page]
    result = []
    for index, text in enumerate(texts, 1):
        block_type = "chapter_title" if page == 9 and index == 1 else "body"
        result.append(
            {
                "block_id": f"p{page:04d}b{index:03d}",
                "block_type": block_type,
                "order": index,
                "text": text,
                "bounding_box": None,
                "confidence": None,
                "uncertain": False,
                "notes": None,
            }
        )
    return result


def _page_payload(page: int, document_id: str, provider: str, model: str) -> dict:
    return {
        "schema_version": "2.1",
        "document_id": document_id,
        "pdf_page": page,
        "provider": provider,
        "model": model,
        "page_type": "chapter_page" if page == 9 else "body_page",
        "printed_page": str(page),
        "title": "CHAPTER II" if page == 9 else None,
        "running_header": f"HEADER {page}",
        "footer": None,
        "page_number_text": str(page),
        "blocks": _blocks(page),
        "continuation_from_previous": None,
        "continuation_to_next": None,
        "boundary_notes": "Adjacent context is required.",
        "uncertain_characters": [],
        "warnings": [],
        "status": "technical_validation_only",
        "translation_ready": False,
    }


EXPECTED = {
    1: (True, "none"),
    2: (False, "section_break"),
    3: (False, "paragraph_break"),
    4: (False, "paragraph_break"),
    5: (True, "none"),
    6: (True, "none"),
    7: (True, "none"),
    8: (False, "chapter_break"),
    9: (True, "none"),
    10: (True, "none"),
}


class MockPhase2BProvider:
    def __init__(self):
        self.calls: list[dict] = []

    def transcribe_images(self, **kwargs):
        self.calls.append(kwargs)
        context = kwargs["context_message"]
        if context.startswith("document_id="):
            page = int(re.search(r"pdf_page=(\d+)", context).group(1))
            document_id = re.search(r"document_id=([^;]+)", context).group(1)
            payload = _page_payload(page, document_id, "mock-zhipu", "mock-glm")
            category = "single"
        else:
            supplied = json.loads(context)
            boundary_id = supplied["boundary_id"]
            previous = int(supplied["previous_page"])
            next_page = int(supplied["next_page"])
            continuation, structural = EXPECTED[previous]
            payload = {
                "schema_version": "1.0",
                "boundary_id": boundary_id,
                "document_id": supplied["document_id"],
                "previous_page": previous,
                "next_page": next_page,
                "previous_last_block_id": supplied["previous_last_block_id"],
                "next_first_block_id": supplied["next_first_block_id"],
                "word_continuation": False,
                "sentence_continuation": continuation,
                "paragraph_continuation": continuation,
                "structural_break": structural,
                "join_operation": "concatenate_with_space" if continuation else "no_join",
                "hyphen_type": "no_hyphen",
                "header_footer_interference": False,
                "reconstructed_boundary_text": "",
                "evidence": ["Mock visible boundary evidence."],
                "confidence": None,
                "needs_triple_review": False,
                "needs_human_review": False,
                "status": "reviewed",
            }
            category = "pair"
        content = json.dumps(payload)
        return ProviderResponse(
            raw_response={
                "id": f"mock-{category}-{len(self.calls)}",
                "model": "mock-glm",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
            content=content,
            request_id=f"mock-{category}-{len(self.calls)}",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            response_model="mock-glm",
        )


def _factory(provider):
    return lambda **kwargs: provider


@pytest.fixture(scope="module")
def mock_phase2b(tmp_path_factory):
    root = tmp_path_factory.mktemp("phase2b-full")
    settings = _settings(root)
    render = render_pages(SAMPLE, settings, pages="1-11", dpi=72, root=root)
    document_id = render.document_id
    raw6 = root / "page6_raw.json"
    atomic_write_json(
        raw6,
        {
            "api_called": True,
            "response": {"choices": [{"message": {"content": json.dumps(_page_payload(6, document_id, settings.vision_provider, settings.vision_model))}}]},
        },
    )
    page6_out = root / "vision_v11" / "mock-zhipu" / "mock-glm" / "page_0006" / "phase2a-cache.json"
    normalize_preserved_response_v11(raw6, page6_out)
    provider = MockPhase2BProvider()
    old_key = os.environ.get(KEY_ENV)
    os.environ[KEY_ENV] = "TEST-ONLY"
    try:
        single = run_single_pages(
            SAMPLE, settings, allow_api=True, confirm_phase2b=True, root=root,
            provider_factory=_factory(provider),
        )
        pair = run_pair_boundaries(
            SAMPLE, settings, allow_api=True, confirm_phase2b=True, root=root,
            provider_factory=_factory(provider),
        )
        open_boundary = create_open_boundary_11_12(SAMPLE, settings, root=root)
        reconstruction = build_logical_blocks(SAMPLE, settings, root=root)
    finally:
        if old_key is None:
            os.environ.pop(KEY_ENV, None)
        else:
            os.environ[KEY_ENV] = old_key
    return root, settings, provider, single, pair, open_boundary, reconstruction


def test_single_plan_reuses_page6_and_calls_at_most_ten(mock_phase2b):
    _, _, provider, single, _, _, _ = mock_phase2b
    assert single.api_calls_started == 10
    assert single.phase2a_cache_hits == 1
    assert single.completed == 11
    assert len([call for call in provider.calls if call["context_message"].startswith("document_id=")]) == 10


def test_exactly_ten_pair_tasks_and_orthogonal_continuation(mock_phase2b):
    root, settings, _, _, pair, _, _ = mock_phase2b
    assert pair.api_calls_started == 10
    assert pair.completed == 10
    decisions = []
    for path in (Path(settings.boundary_normalized_directory) / "pair").rglob("*.json"):
        if path.name.endswith(".validation.json"):
            continue
        decisions.append(BoundaryDecision.model_validate(load_json(path)))
    assert len(decisions) == 10
    five_six = next(item for item in decisions if item.previous_page == 5)
    assert five_six.sentence_continuation is True
    assert five_six.paragraph_continuation is True
    assert five_six.word_continuation is False


def test_chapter_break_never_joins(mock_phase2b):
    _, settings, _, _, _, _, _ = mock_phase2b
    decisions = [
        BoundaryDecision.model_validate(load_json(path))
        for path in (Path(settings.boundary_normalized_directory) / "pair" / "p0008_p0009").glob("*.json")
        if not path.name.endswith(".validation.json")
    ]
    assert decisions[0].structural_break == "chapter_break"
    assert decisions[0].join_operation == "no_join"
    assert decisions[0].reconstructed_boundary_text == ""


def test_open_boundary_11_12_never_calls_api_and_blocks_translation(mock_phase2b):
    _, _, provider, _, _, open_boundary, _ = mock_phase2b
    assert len(provider.calls) == 20
    assert open_boundary.next_page_available is False
    assert open_boundary.missing_required_page == 12
    assert open_boundary.sentence_continuation is True
    assert open_boundary.paragraph_continuation is True
    assert open_boundary.human_review_status == "required"
    assert open_boundary.reconstructed_boundary_text == ""


def test_logical_blocks_cross_pages_and_same_page_can_have_multiple(mock_phase2b):
    _, _, _, _, _, _, result = mock_phase2b
    blocks = [
        LogicalBlock.model_validate(json.loads(line))
        for line in Path(result.logical_blocks_path).read_text(encoding="utf-8").splitlines()
    ]
    assert any(block.source_pages == [7, 8] and block.cross_page for block in blocks)
    assert len([block for block in blocks if 6 in block.source_pages]) >= 3
    assert all(block.source_pages == sorted(block.source_pages) for block in blocks)
    assert all(block.source_block_ids for block in blocks)


def test_page11_final_logical_block_is_incomplete_and_not_ready(mock_phase2b):
    _, _, _, _, _, _, result = mock_phase2b
    blocks = [
        LogicalBlock.model_validate(json.loads(line))
        for line in Path(result.logical_blocks_path).read_text(encoding="utf-8").splitlines()
    ]
    final = [block for block in blocks if 11 in block.source_pages and block.block_type == "body"][-1]
    assert final.sentence_complete is False
    assert final.paragraph_complete is False
    assert final.completeness_status == "incomplete_end"
    assert final.translation_ready is False
    assert "boundary_p0011_p0012_open" in final.unresolved_boundaries


def test_translation_context_is_separate_and_does_not_cross_chapter(mock_phase2b):
    _, _, _, _, _, _, result = mock_phase2b
    units = [
        TranslationUnit.model_validate(json.loads(line))
        for line in Path(result.translation_context_path).read_text(encoding="utf-8").splitlines()
    ]
    assert units
    assert all(unit.translate_target_only is True for unit in units)
    assert all(unit.source_text != unit.context_before_text for unit in units if unit.context_before_text)
    chapter_two = [unit for unit in units if unit.chapter_context == "CHAPTER II"]
    assert chapter_two
    assert all("page eight" not in unit.context_before_text for unit in chapter_two)


def test_translation_ready_gate_cannot_be_bypassed():
    with pytest.raises(ValueError, match="translation_ready"):
        LogicalBlock(
            schema_version="1.0", logical_block_id="x", document_id="d", block_type="body",
            source_pages=[11], page_start=11, page_end=11, cross_page=False,
            source_block_ids=["p0011:b"], source_text="unfinished", boundary_ids=[],
            word_boundary_resolved=False, sentence_complete=False, paragraph_complete=False,
            structural_context={}, merge_reason=[], confidence=None, uncertain_characters=[],
            unresolved_boundaries=["boundary_p0011_p0012_open"], model_review_status="needs_review",
            human_review_status="required", completeness_status="incomplete_end",
            translation_ready=True, created_at="2026-07-15T00:00:00Z",
        )


def test_uncertain_boundary_cannot_create_reconstructed_text():
    with pytest.raises(ValueError, match="Uncertain"):
        BoundaryDecision(
            schema_version="1.0", boundary_id="b", document_id="d", previous_page=1,
            next_page=2, previous_image_sha256="a", next_image_sha256="b",
            previous_last_block_id=None, next_first_block_id=None, previous_tail_text="a",
            next_head_text="b", word_continuation=None, sentence_continuation=True,
            paragraph_continuation=True, structural_break="none", join_operation="uncertain",
            hyphen_type="uncertain", header_footer_interference=None,
            reconstructed_boundary_text="must not exist", evidence=[], confidence=None,
            provider="mock", model="mock", review_window=[1, 2], raw_response_path=None,
            normalization_events=[], model_review_status="needs_review",
            human_review_status="required", status="needs_review",
        )


def test_repeated_run_uses_all_caches_and_zero_new_calls(mock_phase2b):
    root, settings, _, _, _, _, _ = mock_phase2b
    forbidden = lambda **kwargs: (_ for _ in ()).throw(AssertionError("provider must not be created"))
    single = run_single_pages(
        SAMPLE, settings, allow_api=True, confirm_phase2b=True, root=root,
        provider_factory=forbidden,
    )
    pair = run_pair_boundaries(
        SAMPLE, settings, allow_api=True, confirm_phase2b=True, root=root,
        provider_factory=forbidden,
    )
    assert single.api_calls_started == 0 and single.cache_hits == 10
    assert single.phase2a_cache_hits == 1
    assert pair.api_calls_started == 0 and pair.cache_hits == 10


def test_logical_rebuild_is_stable_and_valid(mock_phase2b):
    root, settings, _, _, _, _, first = mock_phase2b
    second = build_logical_blocks(SAMPLE, settings, root=root)
    assert second.logical_block_count == first.logical_block_count
    assert second.translation_ready_true == first.translation_ready_true
    validation = validate_logical_outputs(second.logical_blocks_path, second.translation_context_path)
    assert validation.valid
    assert validation.page11_final_blocked


def test_phase2b_preflight_is_offline_and_under_limits(mock_phase2b, monkeypatch):
    root, settings, _, _, _, _, _ = mock_phase2b
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    report = phase2b_preflight(SAMPLE, settings, root=root)
    assert report.maximum_new_calls == 23
    assert report.triple_calls_maximum == 3
    assert report.single_calls_expected == 0
    assert report.pair_calls_expected == 0
    assert report.estimated_public_price_cny <= report.maximum_estimated_cash_cost_cny
    assert report.deepseek_calls == report.translation_calls == 0
    assert report.api_called is False


def test_full_pdf_is_rejected_for_phase2b_before_processing(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(PermissionError, match="only accepts"):
        phase2b_preflight(FULL, settings, root=tmp_path)


def test_phase2b_records_do_not_contain_secret_or_base64(mock_phase2b):
    root, _, _, _, _, _, _ = mock_phase2b
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*.json")
    )
    assert "TEST-ONLY" not in combined
    assert "data:image/png;base64," not in combined
    assert "DEEPSEEK_API_KEY" not in combined


def test_ledger_enforces_category_and_total_limits(mock_phase2b):
    root, settings, _, _, _, _, _ = mock_phase2b
    ledger = load_json(Path(settings.phase2b_request_directory) / "phase2b_call_ledger.json")
    assert ledger["started"]["single"] == 10
    assert ledger["started"]["pair"] == 10
    assert ledger["started"]["triple"] == 0
    assert ledger["started"]["total"] == 20
    assert all(attempt["retries"] == 0 for attempt in ledger["attempts"])
    assert all(attempt["automatic_retry"] is False for attempt in ledger["attempts"])


def test_triple_selection_only_uses_flagged_pairs_and_never_exceeds_three(mock_phase2b):
    root, settings, _, _, _, _, _ = mock_phase2b
    pair_files = [
        path
        for path in (Path(settings.boundary_normalized_directory) / "pair").rglob("*.json")
        if not path.name.endswith(".validation.json")
    ]
    decisions = [BoundaryDecision.model_validate(load_json(path)) for path in pair_files]
    candidates = {
        item.boundary_id: item.model_copy(update={"needs_triple_review": index < 4})
        for index, item in enumerate(decisions)
    }
    selected = select_triple_candidates(candidates, 3)
    assert len(selected) == 3
    assert all(item.needs_triple_review for item in selected)
    assert [item.previous_page for item in selected] == sorted(item.previous_page for item in selected)


def test_triple_selection_can_include_post_call_qa_mismatch_without_sending_reference(mock_phase2b):
    _, settings, _, _, _, _, _ = mock_phase2b
    pair_files = [
        path
        for path in (Path(settings.boundary_normalized_directory) / "pair").rglob("*.json")
        if not path.name.endswith(".validation.json")
    ]
    decisions = {item.boundary_id: item for item in (BoundaryDecision.model_validate(load_json(path)) for path in pair_files)}
    selected = select_triple_candidates(decisions, 3, {"boundary_p0003_p0004"})
    assert [item.boundary_id for item in selected] == ["boundary_p0003_p0004"]


def test_boundary_raw_and_normalized_results_are_separate(mock_phase2b):
    _, settings, _, _, _, _, _ = mock_phase2b
    raw = list(Path(settings.boundary_raw_directory).rglob("*.json"))
    normalized = [
        path
        for path in Path(settings.boundary_normalized_directory).rglob("*.json")
        if not path.name.endswith(".validation.json")
    ]
    assert len(raw) == 10
    assert len(normalized) >= 11  # ten pairs plus the open 11->12 record
    assert {path.resolve() for path in raw}.isdisjoint(path.resolve() for path in normalized)


def test_human_reference_is_post_call_offline_qa_only(mock_phase2b):
    root, settings, provider, _, _, _, _ = mock_phase2b
    report = run_offline_boundary_qa(settings, root=root)
    assert report.compared == report.matched == 10
    assert report.human_review_required == 0
    assert report.reference_sent_to_model is False
    assert report.model_results_overwritten is False
    sent_context = "\n".join(call["context_message"] for call in provider.calls)
    assert "post_call_offline_qa_only" not in sent_context
    assert "human_review_without_overwriting_model_result" not in sent_context
