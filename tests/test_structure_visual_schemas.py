"""Unit tests for Phase 1C-B visual classification schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bookflow.structure_visual_schemas import (
    FieldEvidence,
    NumberingScheme,
    PageSide,
    VisualBatchRequestManifest,
    VisualClassificationWarning,
    VisualPageClassificationRequest,
    VisualPageClassificationResponse,
    VisualProviderRequest,
    VisualProviderResult,
    VisualRequestContext,
    FORBIDDEN_RESPONSE_FIELDS,
)


def _valid_context(**overrides) -> VisualRequestContext:
    defaults = dict(
        physical_page=1,
        source_page_asset_ref="data/fullbook/pages/test/page_0001.png",
        page_image_sha256="a" * 64,
        pdf_text_length=100,
        pdf_word_count=20,
        embedded_image_count=2,
        ink_coverage=0.05,
        edge_density=0.03,
        current_primary_role="unknown",
        current_content_features=[],
        current_classification_source="offline_heuristic",
        current_evidence_summary=["test evidence"],
        neighboring_prose_pages=[9, 11],
        neighboring_primary_roles=["chapter_body", "chapter_body"],
        target_group="bridge_intervening_unknown",
        requested_visual_questions=["Is this an illustration?"],
    )
    defaults.update(overrides)
    return VisualRequestContext(**defaults)


def _valid_response(**overrides) -> VisualPageClassificationResponse:
    defaults = dict(
        schema_version="1.0",
        physical_page=1,
        primary_role="unknown",
        blank_kind=None,
        content_features=[],
        artifact_overlays=[],
        original_book_content=False,
        contains_prose=False,
        safe_to_exclude_from_prose_flow=True,
        requires_region_analysis=False,
        printed_page_label=None,
        printed_page_number=None,
        numbering_scheme="unknown",
        page_side="unknown",
        field_evidence=[],
        confidence_by_field={"primary_role": 0.5},
        warnings=[],
        reviewer_notes="",
        raw_response_ref="mock://test",
    )
    defaults.update(overrides)
    return VisualPageClassificationResponse(**defaults)


class TestResponseValidation:
    def test_blank_must_have_blank_kind(self):
        with pytest.raises(ValidationError):
            _valid_response(primary_role="blank", blank_kind=None)

    def test_non_blank_must_not_have_blank_kind(self):
        with pytest.raises(ValidationError):
            _valid_response(primary_role="chapter_body", blank_kind="intentional_blank")

    def test_blank_with_blank_kind_ok(self):
        r = _valid_response(primary_role="blank", blank_kind="unknown_blank")
        assert r.blank_kind is not None

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            _valid_response(confidence_by_field={"primary_role": 1.5})

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            _valid_response(unknown_field="bad")

    def test_join_operation_field_rejected(self):
        d = _valid_response().model_dump()
        d["join_operation"] = "insert_space"
        with pytest.raises(ValidationError):
            VisualPageClassificationResponse(**d)

    def test_structural_break_field_rejected(self):
        d = _valid_response().model_dump()
        d["structural_break"] = "chapter_break"
        with pytest.raises(ValidationError):
            VisualPageClassificationResponse(**d)

    def test_from_page_field_rejected(self):
        d = _valid_response().model_dump()
        d["from_page"] = 1
        with pytest.raises(ValidationError):
            VisualPageClassificationResponse(**d)

    def test_page_side_unknown_valid(self):
        r = _valid_response(page_side="unknown")
        assert r.page_side == PageSide.unknown

    def test_printed_page_number_null_valid(self):
        r = _valid_response(printed_page_number=None)
        assert r.printed_page_number is None

    def test_field_evidence_confidence_bounds(self):
        with pytest.raises(ValidationError):
            FieldEvidence(
                field_name="primary_role",
                observed="blank page",
                basis="visual",
                confidence=2.0,
            )

    def test_warning_severity_valid(self):
        w = VisualClassificationWarning(code="low_confidence", message="test", severity="info")
        assert w.severity == "info"


class TestRequestModels:
    def test_valid_request(self):
        ctx = _valid_context()
        req = VisualPageClassificationRequest(
            request_id="visreq_p0001",
            physical_page=1,
            context=ctx,
            request_fingerprint="fp_test",
        )
        assert req.schema_version == "1.0"
        assert req.prompt_version == "v1"

    def test_request_extra_field_rejected(self):
        ctx = _valid_context()
        with pytest.raises(ValidationError):
            VisualPageClassificationRequest(
                request_id="visreq_p0001",
                physical_page=1,
                context=ctx,
                request_fingerprint="fp_test",
                extra="bad",
            )

    def test_batch_manifest_valid(self):
        m = VisualBatchRequestManifest(
            batch_id="test_batch",
            page_count=10,
            physical_pages=list(range(1, 11)),
            created_at="2026-07-16T00:00:00Z",
        )
        assert m.api_calls_allowed is False


class TestProviderModels:
    def test_provider_request_valid(self):
        req = VisualProviderRequest(
            request_id="visreq_p0001",
            physical_page=1,
            source_page_asset_ref="data/test/page.png",
            prompt="Classify this page",
            context_json="{}",
            request_fingerprint="fp_test",
        )
        assert req.schema_version == "1.0"

    def test_provider_result_valid(self):
        resp = _valid_response()
        result = VisualProviderResult(
            request_id="visreq_p0001",
            physical_page=1,
            response=resp,
            raw_content="{}",
            raw_response_ref="mock://test",
        )
        assert result.error is None


class TestForbiddenFields:
    def test_forbidden_fields_set_exists(self):
        assert "join_operation" in FORBIDDEN_RESPONSE_FIELDS
        assert "structural_break" in FORBIDDEN_RESPONSE_FIELDS
        assert "from_page" in FORBIDDEN_RESPONSE_FIELDS
        assert "to_page" in FORBIDDEN_RESPONSE_FIELDS
