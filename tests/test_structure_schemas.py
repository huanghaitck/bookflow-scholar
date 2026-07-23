"""Unit tests for Phase 1B structure schemas (pure, no project data needed)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bookflow.structure_schemas import (
    BlankKind,
    BlankPageDetail,
    BridgeCandidate,
    BridgeCandidateType,
    BridgeEligibility,
    ContentFeature,
    InterveningPageDetail,
    ManualConfirmation,
    PrimaryRole,
    RenderingPolicy,
    StructurePageRecord,
    TextFlowRole,
)


def _valid_page_record(**overrides) -> StructurePageRecord:
    defaults = dict(
        document_id="doc_test",
        pdf_sha256="a" * 64,
        physical_page=1,
        page_side="unknown",
        printed_page_label=None,
        printed_page_number=None,
        numbering_scheme="unknown",
        page_width=400.0,
        page_height=600.0,
        page_rotation=0,
        content_orientation="portrait",
        primary_role="chapter_body",
        content_features=[ContentFeature.prose],
        artifact_overlays=[],
        text_flow_role="prose_anchor",
        rendering_policy=RenderingPolicy(
            preserve_page_record=True,
            include_in_default_output=True,
            include_in_text_flow=True,
            include_in_book_element_order=True,
        ),
        content_bearing=True,
        contains_prose=True,
        original_book_content=True,
        text_flow_fragment_ids=["frag_001"],
        bridge_eligibility="not_applicable",
        requires_region_analysis=False,
        confidence_by_field={"primary_role": 0.9},
        classification_source="cached_vision",
        evidence=["test"],
        processing_status="offline_complete",
        cache_fingerprint="fp_test",
        notes="",
        source_page_asset_ref="data/fullbook/pages/test/page_0001.png",
        page_image_sha256="b" * 64,
        pdf_text_length=100,
        pdf_word_count=20,
        embedded_image_count=0,
        ink_coverage=0.05,
        edge_density=0.03,
    )
    defaults.update(overrides)
    return StructurePageRecord(**defaults)


def _valid_bridge_candidate(**overrides) -> BridgeCandidate:
    defaults = dict(
        candidate_id="bridge_p0001_p0002",
        document_id="doc_test",
        from_page=1,
        from_fragment_id="frag_001",
        to_page=2,
        to_fragment_id="frag_002",
        intervening_pages=[],
        intervening_page_details=[],
        same_section_candidate=True,
        same_chapter_candidate=True,
        bridge_candidate_type="adjacent_prose_pages",
        requires_semantic_boundary_resolution=False,
        candidate_confidence=0.9,
        candidate_source="existing_boundary",
    )
    defaults.update(overrides)
    return BridgeCandidate(**defaults)


class TestStructurePageRecord:
    def test_physical_page_zero_rejected(self):
        with pytest.raises(ValidationError):
            _valid_page_record(physical_page=0)

    def test_physical_page_negative_rejected(self):
        with pytest.raises(ValidationError):
            _valid_page_record(physical_page=-1)

    def test_relative_asset_ref_accepted(self):
        rec = _valid_page_record(
            source_page_asset_ref="data/fullbook/pages/x/page_0001.png"
        )
        assert "data/fullbook" in rec.source_page_asset_ref

    def test_absolute_windows_path_rejected(self):
        with pytest.raises(ValidationError):
            _valid_page_record(source_page_asset_ref="D:/path/to/image.png")

    def test_absolute_unix_path_rejected(self):
        with pytest.raises(ValidationError):
            _valid_page_record(source_page_asset_ref="/absolute/path/image.png")

    def test_empty_asset_ref_rejected(self):
        with pytest.raises(ValidationError):
            _valid_page_record(source_page_asset_ref="")

    def test_backslash_absolute_rejected(self):
        with pytest.raises(ValidationError):
            _valid_page_record(source_page_asset_ref="\\server\\share\\image.png")


class TestRenderingPolicy:
    def test_no_bridge_eligibility_field(self):
        with pytest.raises(ValidationError):
            RenderingPolicy(
                preserve_page_record=True,
                include_in_default_output=True,
                include_in_text_flow=True,
                include_in_book_element_order=True,
                eligible_as_text_flow_bridge=True,
            )

    def test_valid_policy(self):
        rp = RenderingPolicy(
            preserve_page_record=True,
            include_in_default_output=True,
            include_in_text_flow=True,
            include_in_book_element_order=True,
        )
        assert rp.preserve_page_record is True
        assert rp.include_in_faithful_edition_future is False

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            RenderingPolicy(
                preserve_page_record=True,
                include_in_default_output=True,
                include_in_text_flow=True,
                include_in_book_element_order=True,
                unknown_field="bad",
            )


class TestBridgeEligibility:
    def test_values(self):
        assert BridgeEligibility.bridge_capable.value == "bridge_capable"
        assert BridgeEligibility.bridge_blocking.value == "bridge_blocking"
        assert BridgeEligibility.not_applicable.value == "not_applicable"

    def test_rendering_policy_has_no_bridge_field(self):
        assert "eligible_as_text_flow_bridge" not in RenderingPolicy.model_fields
        assert "bridge_eligibility" not in RenderingPolicy.model_fields


class TestBridgeCandidate:
    def test_from_page_less_than_to_page_required(self):
        with pytest.raises(ValidationError):
            _valid_bridge_candidate(from_page=5, to_page=5)

    def test_from_page_greater_than_to_page_rejected(self):
        with pytest.raises(ValidationError):
            _valid_bridge_candidate(from_page=10, to_page=5)

    def test_intervening_consistency(self):
        detail = InterveningPageDetail(
            physical_page=2,
            primary_role=PrimaryRole.blank,
            content_features=[],
            evidence_source="offline_heuristic",
        )
        with pytest.raises(ValidationError):
            _valid_bridge_candidate(
                from_page=1,
                to_page=4,
                intervening_pages=[2, 3],
                intervening_page_details=[detail],
            )

    def test_valid_adjacent_candidate(self):
        c = _valid_bridge_candidate()
        assert c.from_page < c.to_page
        assert c.intervening_pages == []

    def test_candidate_source_includes_existing_boundary(self):
        c = _valid_bridge_candidate(candidate_source="existing_boundary")
        assert c.candidate_source == "existing_boundary"

    def test_candidate_source_includes_manual(self):
        c = _valid_bridge_candidate(candidate_source="manual")
        assert c.candidate_source == "manual"

    def test_manual_confirmation_attachment(self):
        mc = ManualConfirmation(
            confirmed_by="project_owner",
            confirmed_at="2026-07-16T00:00:00Z",
            confirmation_scope="continuous_prose",
            summary="test confirmation",
            evidence_references=["boundary_p0336_p0341"],
            candidate_id="bridge_p0336_p0341",
        )
        c = _valid_bridge_candidate(manual_confirmation=mc)
        assert c.manual_confirmation is not None
        assert c.manual_confirmation.confirmed_by == "project_owner"


class TestBlankKind:
    def test_unknown_blank_exists(self):
        assert BlankKind.unknown_blank.value == "unknown_blank"

    def test_all_kinds(self):
        expected = {
            "intentional_blank", "plate_verso_blank", "scan_blank",
            "watermark_only_blank", "unknown_blank",
        }
        actual = {e.value for e in BlankKind}
        assert actual == expected


class TestBlankPageDetail:
    def test_valid_detail(self):
        d = BlankPageDetail(
            blank_kind=BlankKind.unknown_blank,
            visual_blank_score=0.9,
            ocr_text_length=0,
            ink_coverage=0.005,
            edge_density=0.01,
            known_watermark_only=False,
            blank_confidence=0.6,
            blank_decision_source="offline_heuristic",
            blank_requires_visual_confirmation=True,
        )
        assert d.blank_kind == BlankKind.unknown_blank
        assert d.blank_requires_visual_confirmation is True

    def test_ink_coverage_bounds(self):
        with pytest.raises(ValidationError):
            BlankPageDetail(
                blank_kind=BlankKind.intentional_blank,
                visual_blank_score=0.9,
                ocr_text_length=0,
                ink_coverage=1.5,
                edge_density=0.01,
                known_watermark_only=False,
                blank_confidence=0.6,
                blank_decision_source="offline_heuristic",
                blank_requires_visual_confirmation=True,
            )


class TestUnknownBlankSemantics:
    """Schema-level constraints for unknown_blank pages."""

    def test_unknown_blank_page_can_be_bridge_blocking(self):
        """A page with unknown_blank can have bridge_eligibility=bridge_blocking."""
        rp = RenderingPolicy(
            preserve_page_record=True,
            include_in_default_output=False,
            include_in_text_flow=False,
            include_in_book_element_order=False,
        )
        bd = BlankPageDetail(
            blank_kind=BlankKind.unknown_blank,
            visual_blank_score=0.9,
            ocr_text_length=0,
            ink_coverage=0.005,
            edge_density=0.01,
            known_watermark_only=False,
            blank_confidence=0.6,
            blank_decision_source="offline_heuristic",
            blank_requires_visual_confirmation=True,
        )
        rec = _valid_page_record(
            primary_role="blank",
            contains_prose=False,
            content_features=[],
            bridge_eligibility="bridge_blocking",
            text_flow_role="structural_break",
            rendering_policy=rp,
            content_bearing=False,
            blank_detail=bd,
            requires_followup=True,
        )
        assert rec.bridge_eligibility == BridgeEligibility.bridge_blocking
        assert rec.requires_followup is True
        assert rec.text_flow_role == TextFlowRole.structural_break

    def test_rendering_policy_no_bridge_eligibility_field(self):
        """RenderingPolicy must not have any bridge-related field."""
        assert "eligible_as_text_flow_bridge" not in RenderingPolicy.model_fields
        assert "bridge_eligibility" not in RenderingPolicy.model_fields
        assert "bridge_capable" not in RenderingPolicy.model_fields
        assert "bridge_blocking" not in RenderingPolicy.model_fields


class TestSemanticElementRenderingPolicy:
    """Schema-level tests for semantic element rendering defaults."""

    def test_semantic_element_policy_defaults(self):
        """Semantic elements can be configured with include_in_book_element_order=True."""
        rp = RenderingPolicy(
            preserve_page_record=True,
            include_in_default_output=True,
            include_in_text_flow=False,
            include_in_book_element_order=True,
        )
        assert rp.include_in_book_element_order is True
        assert rp.include_in_default_output is True
        assert rp.include_in_text_flow is False

    def test_blank_policy_defaults(self):
        """Blank pages can be configured with all output flags False."""
        rp = RenderingPolicy(
            preserve_page_record=True,
            include_in_default_output=False,
            include_in_text_flow=False,
            include_in_book_element_order=False,
        )
        assert rp.include_in_book_element_order is False
        assert rp.include_in_default_output is False
