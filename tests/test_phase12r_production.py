from __future__ import annotations

import json
from pathlib import Path

import bookflow.phase12r_production as phase12r
from bookflow.phase12r_production import (
    _adapt_vision_payload, _append_attempt_event, _exception_state,
    _expand_compact_payload, _json_content, _new_attempt,
    _persist_raw_response, _process_raw_attempt, _provider_vision_schema,
    _request_fingerprint, _sync_vision_state, _vision_schema,
    build_front_matter_routing, validate_vision_result,
)
from bookflow.vision_provider import ProviderResponse, ZhipuOpenAICompatibleProvider


def _result() -> dict:
    return {
        "request_id": "p12r_v_009",
        "physical_page": 387,
        "region_id": "appendix_a_rotated_table_p0387",
        "orientation_degrees_clockwise": 90,
        "objects": [{
            "object_id": "table_1", "object_type": "rotated_table", "reading_order": 0,
            "bbox": [0, 0, 100, 50], "confidence": 0.8,
            "transcription_status": "partial", "text": "Measurements",
            "fields": [{"name": "unit", "value": "inches", "status": "confirmed", "bbox": [1, 1, 20, 10]}],
            "rows": [{
                "row_index": 0, "row_type": "data", "bbox": [0, 10, 100, 30],
                "cells": [
                    {"column_index": 0, "text": "Specimen", "status": "confirmed", "bbox": [0, 10, 50, 30]},
                    {"column_index": 1, "text": None, "status": "unresolved", "bbox": [50, 10, 100, 30]},
                ],
            }],
        }],
        "artifacts": [],
        "unresolved": [{"name": "cell_r0_c1", "value": None, "status": "unresolved", "bbox": [50, 10, 100, 30]}],
    }


def test_phase12r_vision_schema_and_rotated_result_validation() -> None:
    request = {
        "request_id": "p12r_v_009", "physical_page": 387,
        "region_id": "appendix_a_rotated_table_p0387",
        "orientation_degrees_clockwise": 90,
    }
    assert _vision_schema()["additionalProperties"] is False
    assert validate_vision_result(_result(), request, width=100, height=50) == []
    invalid = _result()
    invalid["objects"][0]["rows"][0]["cells"][1]["text"] = "17?"
    assert any("unresolved text must be null" in value for value in validate_vision_result(invalid, request, width=100, height=50))
    not_applicable = _result()
    not_applicable["objects"][0]["fields"][0] = {
        "name": "latin_name", "value": None, "status": "not_applicable", "bbox": [0, 0, 0, 0]
    }
    assert validate_vision_result(not_applicable, request, width=100, height=50) == []


def test_provider_can_send_strict_json_schema() -> None:
    captured = {}

    class Completion:
        def create(self, **kwargs):
            captured.update(kwargs)
            return {"id": "req", "model": "configured", "choices": [{"message": {"content": json.dumps(_result())}}]}

    class Client:
        def __init__(self, **_):
            self.chat = type("Chat", (), {"completions": Completion()})()

    provider = ZhipuOpenAICompatibleProvider(
        api_key="redacted", base_url="https://example.invalid", timeout_seconds=1,
        client_factory=Client,
    )
    provider.transcribe_images(
        model="configured", prompt="p", context_message="c",
        image_data_urls=["data:image/png;base64,eA=="], max_output_tokens=10,
        temperature=0, do_sample=False, thinking_mode="disabled",
        response_format_json_object=False, response_json_schema=_vision_schema(),
    )
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True


def test_front_matter_scope_routing_and_frozen_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    model = build_front_matter_routing(root)
    routes = model["routes"]
    counts = model["counts"]
    assert counts["chapter_body_units"] == 821
    assert counts["front_matter_units"] == 150
    assert counts["chapter_body_units"] + counts["front_matter_units"] == 971
    chapters = [item for item in routes if item["section_family"] == "chapter_body"]
    assert all(item["translation_policy"] == "frozen_body" for item in chapters)
    illustrations = [item for item in routes if item["section_id"] == "fm_list_of_illustrations"]
    assert len(illustrations) == 66
    assert all(item["render_policy"] != "render_existing" for item in illustrations)
    preface = [item for item in routes if item["section_id"] == "fm_preface"]
    assert len(preface) == 13 and all(item["translation_policy"] == "reuse_existing" for item in preface)
    artifacts = [item for item in routes if item["translation_policy"] == "non_translatable_artifact"]
    assert artifacts and all(item["render_policy"] == "omit_from_reading" for item in artifacts)


def test_provider_alias_payload_is_normalized_without_inventing_cells() -> None:
    request = {
        "request_id": "p12r_v_003", "physical_page": 381,
        "region_id": "appendix_a_table_p0381", "output_object": "multi_page_table",
    }
    payload = {
        "tables": [{
            "id": "table", "bbox": [0, 0, 100, 50], "confidence": 0.8,
            "rows": [{"bbox": [0, 10, 100, 30], "cells": ["not-provenanced"]}],
        }],
        "artifacts": [], "unresolved": [],
    }
    adapted, changes = _adapt_vision_payload(payload, request, width=100, height=50)
    assert changes == ["collection_alias:tables->objects"]
    assert adapted["objects"][0]["rows"] == []
    assert any(item["name"].endswith("_cells") for item in adapted["unresolved"])
    assert validate_vision_result(adapted, request, width=100, height=50) == []


def test_compact_provider_schema_expands_to_strict_publication_schema() -> None:
    compact = {
        "r": "p12r_v_009", "p": 387, "g": "appendix_a_rotated_table_p0387", "o": 90,
        "x": [{
            "i": "t", "t": "rotated_table", "n": 0, "b": [0, 0, 100, 50], "c": 0.8,
            "s": "partial", "q": "Measurements", "f": [],
            "w": [{"n": 0, "t": "data", "b": [0, 10, 100, 30], "c": [{"n": 0, "t": "A", "s": "confirmed", "b": [0, 10, 50, 30]}]}],
        }], "a": [], "u": [],
    }
    expanded, changes = _expand_compact_payload(compact, {
        "request_id": compact["r"], "physical_page": compact["p"],
        "region_id": compact["g"], "orientation_degrees_clockwise": 90,
        "output_object": "rotated_table",
    })
    assert changes == ["compact_wire_schema_expanded"]
    request = {"request_id": compact["r"], "physical_page": compact["p"], "region_id": compact["g"], "orientation_degrees_clockwise": 90}
    assert validate_vision_result(expanded, request, width=100, height=50) == []
    assert _provider_vision_schema()["required"] == ["x", "a", "u"]


def test_json_adapter_uses_decoder_for_wrapped_single_object() -> None:
    assert _json_content("Result follows:\n{\"ok\":true}\nDone.") == {"ok": True}


def test_minimal_compact_objects_remain_partial_and_tables_unresolved() -> None:
    compact = {
        "r": "p12r_v_007", "p": 385, "g": "appendix_a_table_p0385", "o": "0",
        "x": [{"i": "obj", "t": "table", "n": 0, "b": [0, 0, 100, 50], "c": 0.7, "s": "partial"}],
        "a": [{"i": "head", "t": "running head", "b": [0, 0, 100, 5]}], "u": [],
    }
    request = {"request_id": compact["r"], "physical_page": compact["p"], "region_id": compact["g"], "output_object": "multi_page_table"}
    expanded, _ = _expand_compact_payload(compact, request)
    assert expanded["objects"][0]["rows"] == []
    assert expanded["unresolved"][0]["name"].endswith("_rows")
    assert validate_vision_result(expanded, request, width=100, height=50) == []


def _ledger() -> tuple[dict, dict]:
    manifest = {
        "schema_version": "phase12r-vision-execution-manifest-2.0",
        "status": "in_progress", "planned_requests": 1, "requests": {},
        "api_calls": 0, "api_tokens": 0, "validated_requests": 0,
        "semantic_unresolved": 0, "parse_failures": 0, "schema_failures": 0,
    }
    checkpoint = {
        "status": "in_progress", "stages": {},
        "api_calls": {"vision": 0, "translation": 0, "final_vlm": 0},
        "api_tokens": {"vision": 0, "translation": 0, "final_vlm": 0},
    }
    return manifest, checkpoint


def _request_fixture() -> dict:
    return {
        "request_id": "p12r_test", "physical_page": 387,
        "region_id": "region", "asset_ref": "region.png",
        "output_object": "rotated_table", "orientation_degrees_clockwise": 90,
    }


def _compact_fixture(*, orientation: object = 90, bbox: list[int] | None = None) -> dict:
    box = bbox or [0, 0, 100, 50]
    return {
        "r": "provider_generated_wrong_id", "p": 999, "g": "wrong_region", "o": orientation,
        "x": [{
            "i": "table", "t": "prose", "n": 0, "b": box, "c": 0.8,
            "s": "partial", "q": "Measurements", "f": [],
            "w": [{"n": 0, "t": "data", "b": [0, 10, 100, 30], "c": [
                {"n": 0, "t": "A", "s": "confirmed", "b": [0, 10, 50, 30]},
            ]}],
        }], "a": [], "u": [],
    }


def _response(content: str) -> ProviderResponse:
    return ProviderResponse(
        raw_response={"id": "provider-request", "model": "glm-test", "choices": [{"message": {"content": content}}]},
        content=content, request_id="provider-request", usage={"total_tokens": 12},
        response_model="glm-test", http_status=200,
    )


def test_parse_failure_keeps_immutable_raw_response(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    raw = _persist_raw_response(attempt, _response("{truncated"))
    manifest, checkpoint = _ledger()
    outcome = _process_raw_attempt(
        tmp_path, attempt, _request_fixture(), width=100, height=50,
        fingerprint="fp", manifest=manifest, checkpoint=checkpoint,
    )
    assert outcome == "parse_failed_recoverable"
    assert raw["path"].is_file() and raw["sha256"] == phase12r._sha(raw["path"])
    assert not list(attempt.glob("validated_result_*.json"))


def test_schema_failure_keeps_raw_and_parsed_layers(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    raw = _persist_raw_response(attempt, _response(json.dumps(_compact_fixture(bbox=[0, 0, 0, 0]))))
    manifest, checkpoint = _ledger()
    outcome = _process_raw_attempt(
        tmp_path, attempt, _request_fixture(), width=100, height=50,
        fingerprint="fp", manifest=manifest, checkpoint=checkpoint,
    )
    assert outcome == "schema_failed_recoverable"
    assert raw["path"].is_file()
    assert list(attempt.glob("parsed_candidate_*.json"))


def test_raw_committed_crash_resumes_offline_without_duplicate_attempt(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    _persist_raw_response(attempt, _response(json.dumps(_compact_fixture(orientation="landscape"))))
    manifest, checkpoint = _ledger()
    first = _process_raw_attempt(
        tmp_path, attempt, _request_fixture(), width=100, height=50,
        fingerprint="fp", manifest=manifest, checkpoint=checkpoint,
    )
    event_count = len(list((attempt / "events").glob("*.json")))
    second = _process_raw_attempt(
        tmp_path, attempt, _request_fixture(), width=100, height=50,
        fingerprint="fp", manifest=manifest, checkpoint=checkpoint,
    )
    assert first == second == "validated"
    assert len(list((attempt / "events").glob("*.json"))) == event_count


def test_provider_orientation_and_ids_cannot_override_local_request() -> None:
    request = _request_fixture()
    expanded, _ = _expand_compact_payload(_compact_fixture(orientation="landscape"), request)
    assert expanded["request_id"] == request["request_id"]
    assert expanded["physical_page"] == request["physical_page"]
    assert expanded["region_id"] == request["region_id"]
    assert expanded["orientation_degrees_clockwise"] == 90
    assert expanded["objects"][0]["object_type"] == "rotated_table"


def test_rate_limit_and_timeout_do_not_consume_semantic_budget(tmp_path: Path) -> None:
    class RateLimited(Exception):
        status_code = 429

    class APITimeoutError(Exception):
        pass

    assert _exception_state(RateLimited()) == "rate_limited"
    assert _exception_state(APITimeoutError()) == "transport_failed"
    attempt, metadata = _new_attempt(
        tmp_path, _request_fixture(), {"normalized_bbox": [0, 0, 100, 50]},
        fingerprint="fp", asset_sha="asset", alias="glm", settings={"model": "m", "base_url": "u"},
        transport_retry=0,
    )
    _append_attempt_event(attempt, "rate_limited", semantic_budget_consumed=False)
    assert metadata["semantic_budget_consumed"] is False
    assert phase12r._load(sorted((attempt / "events").glob("*.json"))[-1])["semantic_budget_consumed"] is False


def test_attempts_are_append_only_and_upgrade_changes_fingerprint(tmp_path: Path, monkeypatch) -> None:
    request = _request_fixture()
    region = {"normalized_bbox": [0, 0, 100, 50]}
    settings = {"model": "m", "base_url": "u"}
    first_fp = _request_fingerprint(request, region, "asset", "glm", settings)
    first, _ = _new_attempt(
        tmp_path, request, region, fingerprint=first_fp, asset_sha="asset",
        alias="glm", settings=settings, transport_retry=0,
    )
    monkeypatch.setattr(phase12r, "PROMPT_VERSION", "phase12r-vision-v4-test")
    second_fp = _request_fingerprint(request, region, "asset", "glm", settings)
    second, _ = _new_attempt(
        tmp_path, request, region, fingerprint=second_fp, asset_sha="asset",
        alias="glm", settings=settings, transport_retry=0,
    )
    assert first_fp != second_fp
    assert first != second and (first / "attempt.json").is_file() and (second / "attempt.json").is_file()


def test_checkpoint_and_manifest_are_synchronized(tmp_path: Path) -> None:
    manifest, checkpoint = _ledger()
    manifest["api_calls"] = 7
    manifest["api_tokens"] = 123
    _sync_vision_state(tmp_path, manifest, checkpoint, transition="raw_committed")
    stored_manifest = phase12r._load(tmp_path / phase12r.VISION_MANIFEST_V2)
    stored_checkpoint = phase12r._load(tmp_path / phase12r.CHECKPOINT)
    assert stored_checkpoint["api_calls"]["vision"] == stored_manifest["api_calls"] == 7
    assert stored_checkpoint["api_tokens"]["vision"] == stored_manifest["api_tokens"] == 123
    assert stored_checkpoint["stages"]["vision_extraction"]["last_transition"] == "raw_committed"


def test_compact_adapter_recovers_provider_deviations_deterministically() -> None:
    request = _request_fixture()
    compact = {
        "x": {
            "i": 7, "t": "rotated_table", "n": "visible title",
            "b": [0, 0, 120, 60], "c": [0.8], "s": None,
            "q": None, "f": None, "w": None,
        },
    }
    expanded, changes = _expand_compact_payload(compact, request, width=100, height=50)
    assert "single_compact_object_wrapped" in changes
    assert "bbox_clamped_to_region" in changes
    assert expanded["objects"][0]["object_id"] == "p12r_test_0000"
    assert expanded["objects"][0]["reading_order"] == 0
    assert expanded["objects"][0]["text"] == "visible title"
    assert expanded["objects"][0]["bbox"] == [0, 0, 100, 50]
    assert validate_vision_result(expanded, request, width=100, height=50) == []


def test_missing_provider_bbox_becomes_region_scoped_semantic_unresolved() -> None:
    request = {
        "request_id": "p12r_text", "physical_page": 388,
        "region_id": "region", "output_object": "prose_or_list_entry",
    }
    expanded, changes = _expand_compact_payload(
        {"x": [{"i": 1, "t": "prose", "n": "visible text", "b": [], "s": []}]},
        request, width=100, height=50,
    )
    assert "missing_bbox_mapped_to_region" in changes
    assert expanded["objects"][0]["bbox"] == [0, 0, 100, 50]
    assert validate_vision_result(expanded, request, width=100, height=50) == []
    assert phase12r._is_semantically_unresolved(expanded, request) is True


def test_translation_stage_contracts_preserve_batch_identity() -> None:
    content = json.dumps({"translations": [
        {"translation_unit_id": "u2", "translated_text": "译文二"},
        {"translation_unit_id": "u1", "translated_text": "译文一"},
    ]}, ensure_ascii=False)
    items = phase12r._translation_stage_items(content, "draft", ["u1", "u2"])
    assert [item["translation_unit_id"] for item in items] == ["u1", "u2"]
    review = phase12r._translation_stage_items(json.dumps({"reviews": [
        {"translation_unit_id": "u1", "issues": [], "recommended_translation": "定稿一"},
    ]}, ensure_ascii=False), "review", ["u1"])
    assert review[0]["recommended_translation"] == "定稿一"
    alias_items = phase12r._translation_stage_items(json.dumps({"stage": "draft", "items": [
        {"translation_unit_id": "u1", "translated_text": "译文一"},
    ]}, ensure_ascii=False), "draft", ["u1"])
    assert alias_items[0]["translated_text"] == "译文一"
