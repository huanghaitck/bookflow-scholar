"""Tests for Phase 1C-C live visual classification module.

All tests use fake/mock clients. No real network requests are made.
Covers the 34 required test cases from the task specification.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bookflow.structure_visual_live import (
    MAX_CALLS,
    BatchRunResult,
    ContentError,
    LedgerEntry,
    LiveProviderConfig,
    SystemError_,
    build_live_call_fingerprint,
    build_live_config,
    build_live_payload,
    build_live_system_prompt,
    extract_chat_completion_result,
    load_call_ledger,
    materialize_image_data_url,
    run_live_sample_batch,
    run_preflight,
    save_call_ledger,
    sanitize_raw_response,
    write_live_run_summary,
)
from bookflow.structure_visual_schemas import VisualProviderRequest
from bookflow.paths import load_settings

ROOT = Path(__file__).resolve().parents[1]


def _make_test_png(path):
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9"
        "awAAAABJRU5ErkJggg=="
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes)
    return hashlib.sha256(png_bytes).hexdigest()


def _make_provider_request(page=1, asset_ref="data/test/page.png", img_sha="test_sha"):
    from bookflow.structure_visual_schemas import VisualRequestContext, VisualPageClassificationRequest
    ctx = VisualRequestContext(
        physical_page=page,
        source_page_asset_ref=asset_ref,
        page_image_sha256=img_sha,
        pdf_text_length=0, pdf_word_count=0, embedded_image_count=0,
        ink_coverage=0.0, edge_density=0.0,
        current_primary_role="unknown", current_content_features=[],
        current_classification_source="test", current_evidence_summary=[],
        neighboring_prose_pages=[], neighboring_primary_roles=[],
        target_group="other_unknown", requested_visual_questions=[],
    )
    return VisualPageClassificationRequest(
        request_id=f"visreq_p{page:04d}",
        physical_page=page,
        context=ctx,
        request_fingerprint=f"offline_fp_p{page}",
    )


def _make_mock_raw_response(page=1, content=None):
    if content is None:
        content = json.dumps({
            "schema_version": "1.0", "physical_page": page,
            "primary_role": "unknown", "blank_kind": None,
            "content_features": [], "artifact_overlays": [],
            "original_book_content": False, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [
                {"field_name": "primary_role", "observed": "test",
                 "basis": "visual", "confidence": 0.5}
            ],
            "confidence_by_field": {"primary_role": 0.5},
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
        })
    return {
        "id": f"chatcmpl-test-{page}", "model": "glm-4.6v",
        "created": 1700000000,
        "choices": [{"finish_reason": "stop", "index": 0,
                      "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _make_mock_client(response_dict=None):
    if response_dict is None:
        response_dict = _make_mock_raw_response()
    mock_resp = MagicMock()
    mock_resp.model_dump = MagicMock(return_value=response_dict)
    for k, v in response_dict.items():
        setattr(mock_resp, k, v)
    mock_client = MagicMock()
    mock_client.chat.completions.create = lambda **kw: mock_resp
    return mock_client


class TestImageMaterialization:
    def test_project_relative_path_resolves(self, tmp_path):
        img = tmp_path / "data/test/page.png"
        sha = _make_test_png(img)
        url = materialize_image_data_url(tmp_path, "data/test/page.png", sha)
        assert url.startswith("data:image/png;base64,")

    def test_absolute_path_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Absolute path"):
            materialize_image_data_url(tmp_path, "D:/test/page.png", "x")

    def test_path_escape_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Path escape"):
            materialize_image_data_url(tmp_path, "../escape/page.png", "x")

    def test_sha_mismatch_rejected(self, tmp_path):
        img = tmp_path / "data/test/page.png"
        _make_test_png(img)
        with pytest.raises(ValueError, match="SHA mismatch"):
            materialize_image_data_url(tmp_path, "data/test/page.png", "wrongsha")

    def test_data_url_generated(self, tmp_path):
        img = tmp_path / "data/test/page.png"
        sha = _make_test_png(img)
        url = materialize_image_data_url(tmp_path, "data/test/page.png", sha)
        assert url.startswith("data:")
        assert "base64" in url

    def test_data_url_not_in_persisted_output(self, tmp_path):
        img = tmp_path / "data/test/page.png"
        sha = _make_test_png(img)
        url = materialize_image_data_url(tmp_path, "data/test/page.png", sha)
        for f in tmp_path.rglob("*"):
            if f.is_file():
                content = f.read_text("utf-8", errors="ignore")
                assert url not in content, f"Data URL found in {f}"


class TestSystemPromptAndFingerprint:
    def test_system_prompt_contains_prompt_schema_and_template(self):
        prompt = build_live_system_prompt(ROOT)
        assert "Structure Page Classification Prompt" in prompt
        assert "Response JSON Schema" in prompt
        assert "Response Template" in prompt
        assert "raw_response_ref" in prompt

    def test_system_prompt_fingerprint_deterministic(self):
        from bookflow.structure_visual_live import _compute_system_prompt_sha
        sha1 = _compute_system_prompt_sha(ROOT)
        sha2 = _compute_system_prompt_sha(ROOT)
        assert sha1 == sha2

    def test_live_fingerprint_contains_provider_model_prompt_schema(self):
        fp = build_live_call_fingerprint(
            offline_fingerprint="offline_fp", physical_page=1,
            page_image_sha256="img_sha", prompt_file_sha="prompt_sha",
            system_prompt_sha="sys_prompt_sha", schema_sha="schema_sha",
            provider_id="zhipu", model="glm-4.6v",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            response_format_mode="none", extra_body_profile="none",
        )
        assert isinstance(fp, str) and len(fp) == 64

    def test_different_model_produces_different_fingerprint(self):
        kw = dict(offline_fingerprint="o", physical_page=1,
                  page_image_sha256="i", prompt_file_sha="p",
                  system_prompt_sha="s", schema_sha="sc",
                  provider_id="zhipu",
                  base_url="https://open.bigmodel.cn/api/paas/v4",
                  response_format_mode="none", extra_body_profile="none")
        assert build_live_call_fingerprint(model="glm-4.6v", **kw) != \
               build_live_call_fingerprint(model="gpt-4o", **kw)

    def test_different_prompt_produces_different_fingerprint(self):
        kw = dict(offline_fingerprint="o", physical_page=1,
                  page_image_sha256="i", system_prompt_sha="s",
                  schema_sha="sc", provider_id="zhipu", model="glm-4.6v",
                  base_url="https://open.bigmodel.cn/api/paas/v4",
                  response_format_mode="none", extra_body_profile="none")
        assert build_live_call_fingerprint(prompt_file_sha="a", **kw) != \
               build_live_call_fingerprint(prompt_file_sha="b", **kw)


class TestPayloadConstruction:
    def test_standard_payload_correct(self):
        payload = build_live_payload(
            system_prompt="tp", context_json='{"p":1}',
            image_data_url="data:image/png;base64,abc",
            model="glm-4.6v", max_tokens=4096,
            response_format_json_object=False,
        )
        assert payload["model"] == "glm-4.6v"
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        uc = payload["messages"][1]["content"]
        assert isinstance(uc, list)
        assert uc[0]["type"] == "text"
        assert uc[1]["type"] == "image_url"
        assert "response_format" not in payload

    def test_image_url_has_no_format_field(self):
        payload = build_live_payload(
            system_prompt="t", context_json="{}",
            image_data_url="data:image/png;base64,abc",
            model="m", max_tokens=4096,
            response_format_json_object=False,
        )
        ip = payload["messages"][1]["content"][1]
        assert "image_url" in ip
        assert "format" not in ip["image_url"]

    def test_extra_body_reflects_config_not_mock_defaults(self):
        payload = build_live_payload(
            system_prompt="t", context_json="{}",
            image_data_url="data:image/png;base64,abc",
            model="m", max_tokens=4096,
            response_format_json_object=False,
            do_sample=False, thinking_mode="disabled",
        )
        # extra_body is config-driven, not copied from Mock Provider
        assert payload["extra_body"]["do_sample"] is False
        assert payload["extra_body"]["thinking"]["type"] == "disabled"
        # Must not contain Mock-specific fields beyond do_sample/thinking
        assert "temperature" not in payload


class TestResponseExtraction:
    def test_chat_completion_result_extracted(self):
        raw = _make_mock_raw_response()
        r = extract_chat_completion_result(raw)
        assert r["content"] is not None
        assert r["model"] == "glm-4.6v"
        assert r["provider_request_id"] == "chatcmpl-test-1"
        assert r["usage"]["total_tokens"] == 150

    def test_usage_model_request_id_extracted(self):
        raw = _make_mock_raw_response()
        r = extract_chat_completion_result(raw)
        assert r["usage"]["prompt_tokens"] == 100
        assert r["usage"]["completion_tokens"] == 50
        assert r["provider_request_id"] is not None

    def test_empty_content_is_system_error(self):
        raw = _make_mock_raw_response(content="")
        with pytest.raises(SystemError_, match="empty"):
            extract_chat_completion_result(raw)

    def test_invalid_json_is_content_error(self):
        from bookflow.structure_visual_live import _process_model_response
        with pytest.raises(ContentError, match="not valid JSON"):
            _process_model_response("not json {{{", 1)

    def test_schema_error_not_retried(self):
        from bookflow.structure_visual_live import _process_model_response
        bad = json.dumps({
            "schema_version": "1.0", "physical_page": 1,
            "primary_role": "chapter_body", "blank_kind": "intentional_blank",
            "content_features": [], "artifact_overlays": [],
            "original_book_content": True, "contains_prose": True,
            "safe_to_exclude_from_prose_flow": False,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [], "confidence_by_field": {},
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
        })
        with pytest.raises(ContentError, match="Schema validation"):
            _process_model_response(bad, 1)

    def test_page_mismatch_rejected(self):
        from bookflow.structure_visual_live import _process_model_response
        c = json.dumps({
            "schema_version": "1.0", "physical_page": 99,
            "primary_role": "unknown", "blank_kind": None,
            "content_features": [], "artifact_overlays": [],
            "original_book_content": False, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [
                {"field_name": "p", "observed": "t",
                 "basis": "visual", "confidence": 0.5}],
            "confidence_by_field": {"primary_role": 0.5},
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
        })
        with pytest.raises(ContentError, match="physical_page"):
            _process_model_response(c, 1)

    def test_forbidden_fields_rejected(self):
        from bookflow.structure_visual_live import _process_model_response
        c = json.dumps({
            "schema_version": "1.0", "physical_page": 1,
            "primary_role": "unknown", "blank_kind": None,
            "content_features": [], "artifact_overlays": [],
            "original_book_content": False, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [], "confidence_by_field": {},
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
            "join_operation": "insert_space",
        })
        with pytest.raises(ContentError, match="forbidden field"):
            _process_model_response(c, 1)

    def test_raw_response_ref_overwritten(self):
        from bookflow.structure_visual_live import _process_model_response
        c = json.dumps({
            "schema_version": "1.0", "physical_page": 1,
            "primary_role": "unknown", "blank_kind": None,
            "content_features": [], "artifact_overlays": [],
            "original_book_content": False, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [], "confidence_by_field": {},
            "warnings": [], "reviewer_notes": "",
            "raw_response_ref": "model_says_this",
        })
        resp = _process_model_response(c, 1)
        assert resp.raw_response_ref is None

    def test_api_key_not_in_sanitized_output(self):
        dirty = {
            "id": "t", "choices": [{"message": {"content": "ok"}}],
            "authorization": "Bearer sk-secret-key-12345",
            "nested": {"api_key": "sk-xyz"},
            "data_field": "data:image/png;base64,iVBORw0KGgo=",
        }
        cleaned = sanitize_raw_response(dirty)
        assert cleaned["authorization"] == "[REDACTED]"
        assert cleaned["nested"]["api_key"] == "[REDACTED]"
        assert cleaned["data_field"] == "[REDACTED]"

    def test_authorization_not_in_persisted(self):
        dirty = {"id": "t", "authorization": "Bearer sk-secret",
                 "choices": [{"message": {"content": "ok"}}]}
        cleaned = sanitize_raw_response(dirty)
        s = json.dumps(cleaned)
        assert "Bearer" not in s
        assert "sk-secret" not in s

class TestMarkdownStrippingAndNullFix:
    """Tests for Phase 1D fixes: markdown fence stripping and null confidence handling."""

    def _make_valid_json_str(self, page=1):
        return json.dumps({
            "schema_version": "1.0", "physical_page": page,
            "primary_role": "blank", "blank_kind": "scan_blank",
            "content_features": [], "artifact_overlays": [],
            "original_book_content": False, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [
                {"field_name": "primary_role", "observed": "blank",
                 "basis": "visual", "confidence": 0.9}],
            "confidence_by_field": {"primary_role": 0.9},
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
        })

    def test_markdown_wrapped_json_parsed(self):
        from bookflow.structure_visual_live import _process_model_response
        wrapped = "```json\n" + self._make_valid_json_str(120) + "\n```"
        resp = _process_model_response(wrapped, 120)
        assert resp.primary_role.value == "blank"
        assert resp.physical_page == 120

    def test_markdown_wrapped_json_without_lang(self):
        from bookflow.structure_visual_live import _process_model_response
        wrapped = "```\n" + self._make_valid_json_str(1) + "\n```"
        resp = _process_model_response(wrapped, 1)
        assert resp.primary_role.value == "blank"

    def test_empty_content_classified_as_content_error(self):
        from bookflow.structure_visual_live import _process_model_response
        with pytest.raises(ContentError, match="empty"):
            _process_model_response("", 1)

    def test_literal_null_string_classified_as_content_error(self):
        from bookflow.structure_visual_live import _process_model_response
        with pytest.raises(ContentError, match="literal 'null'"):
            _process_model_response("null", 1)

    def test_null_confidence_values_removed(self):
        from bookflow.structure_visual_live import _process_model_response
        content = json.dumps({
            "schema_version": "1.0", "physical_page": 198,
            "primary_role": "full_page_illustration", "blank_kind": None,
            "content_features": ["illustration"], "artifact_overlays": [],
            "original_book_content": True, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [
                {"field_name": "primary_role", "observed": "illustration",
                 "basis": "visual", "confidence": 0.9}],
            "confidence_by_field": {
                "primary_role": 0.9, "blank_kind": None,
                "printed_page_label": None, "printed_page_number": None,
            },
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
        })
        resp = _process_model_response(content, 198)
        assert "primary_role" in resp.confidence_by_field
        assert "blank_kind" not in resp.confidence_by_field
        assert "printed_page_label" not in resp.confidence_by_field
        assert resp.confidence_by_field["primary_role"] == 0.9

    def test_unknown_complete_object_passes_schema(self):
        from bookflow.structure_visual_live import _process_model_response
        content = json.dumps({
            "schema_version": "1.0", "physical_page": 1,
            "primary_role": "unknown", "blank_kind": None,
            "content_features": [], "artifact_overlays": [],
            "original_book_content": False, "contains_prose": False,
            "safe_to_exclude_from_prose_flow": True,
            "requires_region_analysis": False,
            "printed_page_label": None, "printed_page_number": None,
            "numbering_scheme": "unknown", "page_side": "unknown",
            "field_evidence": [], "confidence_by_field": {},
            "warnings": [], "reviewer_notes": "", "raw_response_ref": None,
        })
        resp = _process_model_response(content, 1)
        assert resp.primary_role.value == "unknown"

    def test_prompt_forbids_markdown_and_null(self):
        from bookflow.structure_visual_live import _load_prompt_file
        prompt = _load_prompt_file(ROOT)
        assert "Do NOT wrap" in prompt
        assert "markdown code fences" in prompt
        assert "null" in prompt.lower()
        assert "confidence_by_field" in prompt

    def test_live_fingerprint_changes_with_prompt_fix(self):
        from bookflow.structure_visual_live import _compute_prompt_file_sha
        sha = _compute_prompt_file_sha(ROOT)
        # The new prompt SHA must differ from the old one
        assert sha != "a6436d8893467a0c3ae7f925e89602351da2de421f3ffc91c6f05d6816bdf1ab"


class TestCallPolicy:
    def test_max_calls_is_10(self):
        assert MAX_CALLS == 10

    def test_success_fingerprint_match_skips(self, tmp_path):
        img = tmp_path / "data/test/page_0001.png"
        sha = _make_test_png(img)
        req = _make_provider_request(1, "data/test/page_0001.png", sha)
        settings = load_settings()
        config = build_live_config(settings)
        from bookflow.structure_visual_live import _compute_live_fp_for_req
        real_fp = _compute_live_fp_for_req(req, "p", "s", "sc", config)
        ledger = {1: LedgerEntry(
            request_id="r1", physical_page=1,
            offline_request_fingerprint="offline_fp_p1",
            live_call_fingerprint=real_fp, status="success",
            attempted=True, api_call_count=1, automatic_retry_count=0)}
        result = run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req], ledger=ledger,
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=lambda **kw: _make_mock_client(), dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert 1 in result.skipped_existing
        assert result.actual_api_calls == 0

    def test_fingerprint_changed_does_not_skip(self, tmp_path):
        img = tmp_path / "data/test/page_0001.png"
        sha = _make_test_png(img)
        req = _make_provider_request(1, "data/test/page_0001.png", sha)
        ledger = {1: LedgerEntry(
            request_id="r1", physical_page=1,
            offline_request_fingerprint="offline_fp_p1",
            live_call_fingerprint="OLD_DIFFERENT_FP", status="success",
            attempted=True, api_call_count=1, automatic_retry_count=0)}
        settings = load_settings()
        config = build_live_config(settings)
        result = run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req], ledger=ledger,
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=lambda **kw: _make_mock_client(), dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert 1 in result.attempted
        assert result.actual_api_calls == 1

    def test_system_error_stops_batch(self, tmp_path):
        img1 = tmp_path / "data/test/page_0001.png"
        sha1 = _make_test_png(img1)
        img2 = tmp_path / "data/test/page_0002.png"
        sha2 = _make_test_png(img2)
        req1 = _make_provider_request(1, "data/test/page_0001.png", sha1)
        req2 = _make_provider_request(2, "data/test/page_0002.png", sha2)
        cc = [0]
        def failing_factory(**kw):
            mc = MagicMock()
            def fc(**k):
                cc[0] += 1
                if cc[0] == 1:
                    raise ConnectionError("network error")
                return MagicMock()
            mc.chat.completions.create = fc
            return mc
        settings = load_settings()
        config = build_live_config(settings)
        result = run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req1, req2], ledger={},
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=failing_factory, dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert 1 in result.system_errors
        assert result.stopped_due_to_system_error
        assert 2 in result.not_attempted
        assert result.actual_api_calls == 1

    def test_content_error_continues_to_next_page(self, tmp_path):
        img1 = tmp_path / "data/test/page_0001.png"
        sha1 = _make_test_png(img1)
        img2 = tmp_path / "data/test/page_0002.png"
        sha2 = _make_test_png(img2)
        req1 = _make_provider_request(1, "data/test/page_0001.png", sha1)
        req2 = _make_provider_request(2, "data/test/page_0002.png", sha2)
        cc = [0]
        def mixed_factory(**kw):
            mc = MagicMock()
            def fc(**k):
                cc[0] += 1
                if cc[0] == 1:
                    r = _make_mock_raw_response(1, content="not json {{{")
                    mr = MagicMock()
                    mr.model_dump = MagicMock(return_value=r)
                    return mr
                else:
                    r = _make_mock_raw_response(2)
                    mr = MagicMock()
                    mr.model_dump = MagicMock(return_value=r)
                    return mr
            mc.chat.completions.create = fc
            return mc
        settings = load_settings()
        config = build_live_config(settings)
        result = run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req1, req2], ledger={},
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=mixed_factory, dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert 1 in result.content_errors
        assert 2 in result.succeeded
        assert not result.stopped_due_to_system_error

    def test_automatic_retry_count_always_zero(self, tmp_path):
        img = tmp_path / "data/test/page_0001.png"
        sha = _make_test_png(img)
        req = _make_provider_request(1, "data/test/page_0001.png", sha)
        settings = load_settings()
        config = build_live_config(settings)
        result = run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req], ledger={},
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=lambda **kw: _make_mock_client(), dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert result.automatic_retry_count == 0

    def test_attempted_content_error_not_auto_recalled(self, tmp_path):
        img = tmp_path / "data/test/page_0001.png"
        sha = _make_test_png(img)
        req = _make_provider_request(1, "data/test/page_0001.png", sha)
        ledger = {1: LedgerEntry(
            request_id="r1", physical_page=1,
            offline_request_fingerprint="offline_fp_p1",
            live_call_fingerprint="some_fp", status="content_error",
            attempted=True, api_call_count=1, automatic_retry_count=0)}
        settings = load_settings()
        config = build_live_config(settings)
        result = run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req], ledger=ledger,
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=lambda **kw: _make_mock_client(), dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert 1 in result.attempted


class TestLedgerPersistence:
    def test_call_ledger_atomic_recovery(self, tmp_path):
        lp = tmp_path / "call_ledger.json"
        entries = {
            1: LedgerEntry("r1", 1, "fp1", "lfp1", "success", True, 1, 0),
            2: LedgerEntry("r2", 2, "fp2", "lfp2", "content_error", True, 1, 0),
        }
        save_call_ledger(lp, entries)
        loaded = load_call_ledger(lp)
        assert len(loaded) == 2
        assert loaded[1].status == "success"
        assert loaded[2].status == "content_error"


class TestPathSafety:
    def test_all_persisted_paths_project_relative(self, tmp_path):
        img = tmp_path / "data/test/page_0001.png"
        sha = _make_test_png(img)
        req = _make_provider_request(1, "data/test/page_0001.png", sha)
        settings = load_settings()
        config = build_live_config(settings)
        run_live_sample_batch(
            root=tmp_path, settings=settings, config=config,
            requests=[req], ledger={},
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=lambda **kw: _make_mock_client(), dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        lp = tmp_path / "data/fullbook/structure/phase1c/live/sample_batch_v1/call_ledger.json"
        ledger = load_call_ledger(lp)
        for e in ledger.values():
            for ref in [e.normalized_response_ref, e.raw_response_ref,
                        e.usage_ref, e.error_ref]:
                if ref:
                    assert ":/" not in ref
                    assert not ref.startswith("/")


class TestNetworkBlocking:
    def test_no_socket_http_calls_during_mock(self, monkeypatch):
        orig = socket.socket
        def block(*a, **k):
            raise RuntimeError("Network blocked")
        monkeypatch.setattr(socket, "socket", block)
        import tempfile
        tp = Path(tempfile.mkdtemp())
        img = tp / "data/test/page_0001.png"
        sha = _make_test_png(img)
        req = _make_provider_request(1, "data/test/page_0001.png", sha)
        settings = load_settings()
        config = build_live_config(settings)
        result = run_live_sample_batch(
            root=tp, settings=settings, config=config,
            requests=[req], ledger={},
            prompt_file_sha="p", system_prompt_sha="s", schema_sha="sc",
            client_factory=lambda **kw: _make_mock_client(), dry_run=False,
            system_prompt=build_live_system_prompt(ROOT), api_key="test_key")
        assert 1 in result.succeeded
        monkeypatch.setattr(socket, "socket", orig)


class TestRunSummary:
    def test_run_summary_fields(self, tmp_path):
        config = LiveProviderConfig(
            provider_id="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4.6v", api_key_env="ZAI_API_KEY",
            timeout_seconds=180, max_calls=10,
            response_format_json_object=False, max_output_tokens=4096)
        result = BatchRunResult(
            planned_pages=[1, 8], already_successful=[],
            pending_at_start=[1, 8], attempted=[1, 8],
            succeeded=[1, 8], content_errors=[], system_errors=[],
            skipped_existing=[], not_attempted=[],
            actual_api_calls=2, automatic_retry_count=0,
            total_prompt_tokens=200, total_completion_tokens=100,
            total_tokens=300, stopped_due_to_system_error=False,
            preflight_ok=True, preflight_summary="ok")
        sp = tmp_path / "run_summary.json"
        write_live_run_summary(
            sp, config=config, result=result,
            prompt_sha="p", schema_sha="sc",
            started_at="2026-07-17T00:00:00Z",
            completed_at="2026-07-17T00:01:00Z")
        data = json.loads(sp.read_text("utf-8"))
        assert data["review_status"] == "pending_human_review"
        assert data["formal_structure_modified"] is False
        assert data["boundaries_modified"] is False
        assert data["api_key_logged"] is False
        assert data["data_url_persisted"] is False
        assert data["actual_api_call_count"] == 2
        assert data["automatic_retry_count"] == 0
        assert data["max_calls"] == 10
        assert data["sanitized_base_url"] == "open.bigmodel.cn"
