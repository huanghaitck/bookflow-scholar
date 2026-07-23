from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.io_utils import load_json, sha256_file
from bookflow.paths import project_root
from bookflow.phase2a1 import VisionNormalizedPageV11, normalize_preserved_response_v11


ROOT = project_root()
REAL_RAW = ROOT / "data" / "vision_raw" / "zhipu" / "glm-4.6v" / "page_0006" / "7fe25e03e931baeb062be628f615aca8a19400ac04f76c2f67cb0af88a40ffc0.json"
REAL_OLD = ROOT / "data" / "vision_normalized" / "zhipu" / "glm-4.6v" / "page_0006" / "7fe25e03e931baeb062be628f615aca8a19400ac04f76c2f67cb0af88a40ffc0.json"


def _payload(**changes):
    value = {
        "schema_version": "2.0",
        "document_id": "doc_test",
        "pdf_page": 6,
        "provider": "test",
        "model": "test-model",
        "page_type": "body_page",
        "printed_page": 6,
        "title": None,
        "running_header": "HEADER",
        "footer": None,
        "page_number_text": "6",
        "blocks": [
            {
                "block_id": "b1",
                "block_type": "body",
                "order": 1,
                "text": "Visible text.",
                "bounding_box": None,
                "confidence": None,
                "uncertain": False,
                "notes": None,
            }
        ],
        "continuation_from_previous": True,
        "continuation_to_next": False,
        "boundary_notes": "Visible evidence.",
        "uncertain_characters": [],
        "warnings": [],
        "status": "technical_validation_only",
        "translation_ready": False,
    }
    value.update(changes)
    return value


def _raw(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "raw.json"
    path.write_text(
        json.dumps(
            {
                "api_called": True,
                "response": {"choices": [{"message": {"content": json.dumps(payload)}}]},
            }
        ),
        encoding="utf-8",
    )
    return path


def _normalize(tmp_path: Path, payload: dict, **kwargs) -> VisionNormalizedPageV11:
    return normalize_preserved_response_v11(
        _raw(tmp_path, payload), tmp_path / "v11.json", **kwargs
    )


@pytest.mark.parametrize(
    ("value", "expected"), [(6, "6"), ("vi", "vi"), ("Plate 6", "Plate 6"), (None, None)]
)
def test_printed_page_safe_normalization(tmp_path, value, expected):
    result = _normalize(tmp_path, _payload(printed_page=value))
    assert result.printed_page == expected
    if isinstance(value, int):
        assert any(event.action == "integer_to_decimal_string" for event in result.normalization_events)


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_uncertain_characters_become_list(tmp_path, value):
    result = _normalize(tmp_path, _payload(uncertain_characters=value))
    assert result.uncertain_characters == []
    assert any(event.field == "uncertain_characters" for event in result.normalization_events)


def test_nonempty_uncertain_string_is_preserved_and_requires_review(tmp_path):
    result = _normalize(tmp_path, _payload(uncertain_characters="[?]"))
    assert result.uncertain_characters == ["[?]"]
    assert result.status == "needs_review"
    assert any(event.requires_review for event in result.normalization_events)


def test_missing_continuation_does_not_default_false(tmp_path):
    payload = _payload()
    del payload["continuation_from_previous"]
    result = _normalize(tmp_path, payload)
    assert result.continuation_from_previous is None
    assert result.boundary_status == "needs_adjacent_context"
    assert result.boundary_review_required is True


@pytest.mark.parametrize("value", [True, False, None])
def test_continuation_supports_three_states(tmp_path, value):
    result = _normalize(tmp_path, _payload(continuation_to_next=value))
    assert result.continuation_to_next is value


def test_original_and_previous_result_are_never_overwritten(tmp_path):
    raw = _raw(tmp_path, _payload())
    previous = tmp_path / "old.json"
    previous.write_text('{"old": true}', encoding="utf-8")
    raw_before = sha256_file(raw)
    previous_before = sha256_file(previous)
    output = tmp_path / "new" / "v11.json"
    normalize_preserved_response_v11(raw, output, previous_normalized_path=previous)
    assert sha256_file(raw) == raw_before
    assert sha256_file(previous) == previous_before
    assert output.is_file()
    assert output != previous


def test_real_page6_is_re_normalized_offline_without_translation(tmp_path):
    raw_before = sha256_file(REAL_RAW)
    old_before = sha256_file(REAL_OLD)
    output = tmp_path / "page6-v11.json"
    result = normalize_preserved_response_v11(
        REAL_RAW,
        output,
        previous_normalized_path=REAL_OLD,
        force_adjacent_review={"continuation_from_previous"},
    )
    saved = VisionNormalizedPageV11.model_validate(load_json(output))
    assert result.pdf_page == saved.pdf_page == 6
    assert saved.printed_page == "6"
    assert saved.uncertain_characters == []
    assert saved.continuation_from_previous is None
    assert saved.boundary_review_required is True
    assert saved.translation_ready is False
    assert saved.api_called is True
    assert sha256_file(REAL_RAW) == raw_before
    assert sha256_file(REAL_OLD) == old_before


def test_schema_patch_output_cannot_replace_raw_or_old(tmp_path):
    raw = _raw(tmp_path, _payload())
    with pytest.raises(ValueError, match="new path"):
        normalize_preserved_response_v11(raw, raw)


def test_numeric_block_id_is_losslessly_converted_and_recorded(tmp_path):
    payload = _payload()
    payload["blocks"][0]["block_id"] = 1
    result = _normalize(tmp_path, payload)
    assert result.blocks[0].block_id == "1"
    assert any(event.field == "blocks[0].block_id" for event in result.normalization_events)


@pytest.mark.parametrize("model_type", ["title", "watermark"])
def test_unknown_model_block_types_are_preserved_as_unknown(tmp_path, model_type):
    payload = _payload()
    payload["blocks"][0]["block_type"] = model_type
    result = _normalize(tmp_path, payload)
    assert result.blocks[0].block_type == "unknown"
    event = next(event for event in result.normalization_events if event.field == "blocks[0].block_type")
    assert event.original_value == model_type
    assert event.normalized_value == "unknown"


def test_null_block_text_becomes_empty_without_inventing_text(tmp_path):
    payload = _payload()
    payload["blocks"][0]["text"] = None
    result = _normalize(tmp_path, payload)
    assert result.blocks[0].text == ""
    assert any(event.action == "null_to_empty_string" for event in result.normalization_events)


def test_label_confidence_is_recorded_and_not_fabricated(tmp_path):
    payload = _payload()
    payload["blocks"][0]["confidence"] = "high"
    result = _normalize(tmp_path, payload)
    assert result.blocks[0].confidence is None
    event = next(event for event in result.normalization_events if event.field == "blocks[0].confidence")
    assert event.original_value == "high"
    assert event.normalized_value is None
