from __future__ import annotations

import json
from pathlib import Path

from bookflow.fullbook_pipeline import FullbookCheckpointStore, run_fullbook_vision_batch
from bookflow.io_utils import atomic_write_json, load_json, sha256_file
from bookflow.paths import load_settings
from bookflow.production_recovery import (
    close_ambiguous_inflight,
    derive_effective_text_boundaries,
    finalize_human_verified_blank,
    find_dynamic_vision_resume_page,
    run_manual_page_recovery,
)
from bookflow.vision_provider import ProviderResponse


class _RecoveryProvider:
    calls = 0

    def __init__(self, **_: object) -> None:
        pass

    def transcribe_one_page(self, **kwargs: object) -> ProviderResponse:
        type(self).calls += 1
        context = str(kwargs["context_message"])
        page = int(context.split("pdf_page=", 1)[1].split(";", 1)[0])
        payload = {
            "schema_version": "2.0", "document_id": "doc_full", "pdf_page": page,
            "provider": "zhipu", "model": "glm-4.6v", "page_type": "body",
            "printed_page": str(page), "title": None, "running_header": None,
            "footer": None, "page_number_text": str(page),
            "blocks": [{"block_id": f"p{page}_b1", "block_type": "body", "order": 1,
                        "text": f"Recovered page {page}.", "bounding_box": None,
                        "confidence": None, "uncertain": False, "notes": None}],
            "continuation_from_previous": None, "continuation_to_next": None,
            "boundary_notes": "Adjacent context required.", "uncertain_characters": [],
            "warnings": [], "status": "technical_validation_only", "translation_ready": False,
        }
        content = json.dumps(payload)
        raw = {"id": f"recovery-{page}", "model": "glm-4.6v",
               "choices": [{"finish_reason": "stop", "message": {"content": content}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        return ProviderResponse(raw_response=raw, content=content, request_id=raw["id"],
                                usage=raw["usage"], response_model="glm-4.6v")


def _fixture(tmp_path: Path, pages: int = 4):
    prompt = tmp_path / "prompts/vision_transcription_v2.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("verified vision prompt", encoding="utf-8")
    records = tmp_path / "data/fullbook/page_manifests/book/profile/pages"
    images = tmp_path / "data/fullbook/pages/book/profile"
    records.mkdir(parents=True)
    images.mkdir(parents=True)
    for page in range(1, pages + 1):
        image = images / f"page_{page:04d}.png"
        image.write_bytes(f"image-{page}".encode())
        atomic_write_json(records / f"page_{page:04d}.json", {
            "document_id": "doc_full", "pdf_page": page, "image_path": str(image),
            "image_sha256": sha256_file(image), "source_pdf_sha256": "fullhash",
        })
    ledger = tmp_path / "data/fullbook/vision/call_ledger.json"
    atomic_write_json(ledger, {
        "schema_version": "fullbook-vision-ledger-1.0", "source_pdf_sha256": "fullhash",
        "real_calls_started": 2,
        "attempts": [
            {"pdf_page": 2, "request_fingerprint": "failed-old", "status": "failed",
             "started_at": "2026-01-01T00:00:00Z", "automatic_retry": False},
            {"pdf_page": 3, "request_fingerprint": "lost-old", "status": "in_flight",
             "started_at": "2026-01-01T00:01:00Z", "automatic_retry": False},
        ],
    })
    checkpoint = FullbookCheckpointStore(
        tmp_path / "data/fullbook/checkpoints/production.json", source_pdf_sha256="fullhash"
    )
    checkpoint.mark_quarantine("vision_single", "page_0002", "connection error")
    settings = load_settings().model_copy(update={"vision_api_enabled": True})
    return settings, ledger


def test_human_verified_blank_is_terminal_without_api_or_fragments(tmp_path: Path) -> None:
    settings, ledger = _fixture(tmp_path)
    before = load_json(ledger)["real_calls_started"]
    state = finalize_human_verified_blank(settings, pdf_page=1, root=tmp_path)

    assert state.resolution_type == "human_verified_blank_page"
    assert state.transcription_status == "not_required_blank"
    assert state.text_blocks == [] and state.source_fragments == []
    assert state.translation_ready is False and state.quarantine is False
    assert load_json(ledger)["real_calls_started"] == before
    _RecoveryProvider.calls = 0
    replay = run_fullbook_vision_batch(
        settings, pages=[1], root=tmp_path, allow_api=True,
        provider_factory=_RecoveryProvider, api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    assert replay.api_calls_this_run == 0
    assert _RecoveryProvider.calls == 0


def test_ambiguous_inflight_is_closed_without_losing_original_state(tmp_path: Path) -> None:
    settings, ledger = _fixture(tmp_path)
    closed = close_ambiguous_inflight(settings, pdf_page=3, root=tmp_path)

    attempt = load_json(ledger)["attempts"][1]
    assert closed.final_attempt_status == "ambiguous_abandoned"
    assert attempt["status"] == "ambiguous_abandoned"
    assert attempt["original_status"] == "in_flight"
    assert attempt["request_fingerprint"] == "lost-old"
    assert attempt["started_at"] == "2026-01-01T00:01:00Z"


def test_failed_and_ambiguous_pages_get_at_most_one_manual_recovery(tmp_path: Path) -> None:
    settings, ledger = _fixture(tmp_path)
    close_ambiguous_inflight(settings, pdf_page=3, root=tmp_path)
    _RecoveryProvider.calls = 0

    first = run_manual_page_recovery(
        settings, pdf_page=2, recovery_reason="explicit_recovery_after_connection_error",
        root=tmp_path, allow_api=True, provider_factory=_RecoveryProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    second = run_manual_page_recovery(
        settings, pdf_page=2, recovery_reason="explicit_recovery_after_connection_error",
        root=tmp_path, allow_api=False, provider_factory=_RecoveryProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    third = run_manual_page_recovery(
        settings, pdf_page=3, recovery_reason="lost_response_after_client_session_interruption",
        root=tmp_path, allow_api=True, provider_factory=_RecoveryProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )

    assert first.api_calls_this_run == 1 and second.api_calls_this_run == 0
    assert third.api_calls_this_run == 1
    assert _RecoveryProvider.calls == 2
    attempts = load_json(ledger)["attempts"]
    recoveries = [item for item in attempts if item.get("attempt_type") == "manual_recovery"]
    assert len(recoveries) == 2
    assert all(item["automatic_retry"] is False for item in recoveries)
    assert all(item["status"] == "completed" for item in recoveries)


def test_dynamic_resume_skips_completed_blank_and_quarantine(tmp_path: Path) -> None:
    settings, _ = _fixture(tmp_path)
    finalize_human_verified_blank(settings, pdf_page=1, root=tmp_path)
    close_ambiguous_inflight(settings, pdf_page=3, root=tmp_path)
    _RecoveryProvider.calls = 0
    run_manual_page_recovery(
        settings, pdf_page=2, recovery_reason="explicit_recovery_after_connection_error",
        root=tmp_path, allow_api=True, provider_factory=_RecoveryProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )
    run_manual_page_recovery(
        settings, pdf_page=3, recovery_reason="lost_response_after_client_session_interruption",
        root=tmp_path, allow_api=True, provider_factory=_RecoveryProvider,
        api_key_loader=lambda *_: ("secret", "TEST_KEY"),
    )

    assert find_dynamic_vision_resume_page(settings, actual_page_count=4, root=tmp_path) == 4


def test_nontext_pages_form_effective_textual_adjacency() -> None:
    boundaries = derive_effective_text_boundaries([
        {"pdf_page": 196, "page_type": "body", "has_text": True},
        {"pdf_page": 197, "page_type": "illustration", "has_text": False},
        {"pdf_page": 198, "page_type": "blank", "has_text": False},
        {"pdf_page": 199, "page_type": "body", "has_text": True},
    ])

    assert boundaries == [{
        "previous_text_page": 196, "next_text_page": 199,
        "boundary_kind": "textual_adjacency_across_nontext_pages",
        "physical_pages_skipped": [197, 198],
    }]

