"""Tests for Phase 1 Final structure override application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bookflow.structure_final import (
    generate_final_page_map,
    generate_section_candidates,
    generate_book_manifest,
    verify_gate,
    _derive_text_flow_role,
    _derive_bridge_eligibility,
    _derive_rendering_policy,
    _derive_content_bearing,
    _sanitize_content_features,
    _build_blank_detail,
)


@pytest.fixture
def project_root():
    return Path(".").resolve()


class TestDerivationRules:
    def test_chapter_open_text_flow(self):
        assert _derive_text_flow_role("chapter_open", True) == "prose_anchor"

    def test_chapter_body_text_flow(self):
        assert _derive_text_flow_role("chapter_body", True) == "prose_continuation"

    def test_blank_text_flow(self):
        assert _derive_text_flow_role("blank", False) == "non_prose_bridge"

    def test_illustration_text_flow(self):
        assert _derive_text_flow_role("full_page_illustration", False) == "non_prose_bridge"

    def test_title_page_text_flow(self):
        assert _derive_text_flow_role("title_page", False) == "structural_break"

    def test_appendix_with_prose_text_flow(self):
        assert _derive_text_flow_role("appendix", True) == "prose_continuation"

    def test_appendix_without_prose_text_flow(self):
        assert _derive_text_flow_role("appendix", False) == "text_flow_none"

    def test_chapter_body_bridge(self):
        assert _derive_bridge_eligibility("chapter_body", None) == "not_applicable"

    def test_illustration_bridge(self):
        assert _derive_bridge_eligibility("full_page_illustration", None) == "bridge_capable"

    def test_blank_bridge_capable(self):
        assert _derive_bridge_eligibility("blank", "plate_verso_blank") == "bridge_capable"

    def test_unknown_blank_bridge_blocking(self):
        assert _derive_bridge_eligibility("blank", "unknown_blank") == "bridge_blocking"

    def test_chapter_open_bridge_blocking(self):
        assert _derive_bridge_eligibility("chapter_open", None) == "bridge_blocking"

    def test_chapter_body_rendering(self):
        rp = _derive_rendering_policy("chapter_body")
        assert rp["include_in_default_output"] is True
        assert rp["include_in_text_flow"] is True
        assert rp["include_in_book_element_order"] is True

    def test_blank_rendering(self):
        rp = _derive_rendering_policy("blank")
        assert rp["include_in_default_output"] is False
        assert rp["include_in_text_flow"] is False
        assert rp["include_in_book_element_order"] is False

    def test_illustration_rendering(self):
        rp = _derive_rendering_policy("full_page_illustration")
        assert rp["include_in_default_output"] is True
        assert rp["include_in_text_flow"] is False
        assert rp["include_in_book_element_order"] is True

    def test_appendix_rendering(self):
        rp = _derive_rendering_policy("appendix")
        assert rp["include_in_default_output"] is True
        assert rp["include_in_book_element_order"] is True

    def test_chapter_body_content_bearing(self):
        assert _derive_content_bearing("chapter_body", True) is True

    def test_blank_content_bearing(self):
        assert _derive_content_bearing("blank", False) is False

    def test_title_page_content_bearing(self):
        assert _derive_content_bearing("title_page", False) is True


class TestSanitizeContentFeatures:
    def test_barcode_moved_to_overlays(self):
        record = {
            "content_features": ["prose", "barcode", "page_number"],
            "artifact_overlays": [],
        }
        result = _sanitize_content_features(record)
        assert "barcode" not in result["content_features"]
        assert "barcode" in result["artifact_overlays"]
        assert "prose" in result["content_features"]

    def test_library_stamp_stays_in_content(self):
        record = {
            "content_features": ["library_stamp"],
            "artifact_overlays": [],
        }
        result = _sanitize_content_features(record)
        assert "library_stamp" in result["content_features"]

    def test_no_change_when_clean(self):
        record = {
            "content_features": ["prose", "table"],
            "artifact_overlays": ["digitization_watermark"],
        }
        result = _sanitize_content_features(record)
        assert result["content_features"] == ["prose", "table"]
        assert result["artifact_overlays"] == ["digitization_watermark"]


class TestBlankDetail:
    def test_plate_verso_blank_detail(self):
        detail = _build_blank_detail("plate_verso_blank")
        assert detail["blank_kind"] == "plate_verso_blank"
        assert detail["blank_decision_source"] == "manual"

    def test_none_returns_none(self):
        assert _build_blank_detail(None) is None

    def test_watermark_only(self):
        detail = _build_blank_detail("watermark_only_blank")
        assert detail["known_watermark_only"] is True


class TestFinalPageMapGeneration:
    def test_generates_412_records(self, project_root):
        path, stats = generate_final_page_map(project_root)
        assert path.is_file()
        lines = path.read_text("utf-8").splitlines()
        records = [json.loads(l) for l in lines if l.strip()]
        assert len(records) == 412

    def test_pages_1_to_412(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        pages = [r["physical_page"] for r in records]
        assert pages == list(range(1, 413))

    def test_no_duplicates(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        pages = [r["physical_page"] for r in records]
        assert len(pages) == len(set(pages))

    def test_30_chapter_open(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapter_open = [r for r in records if r["primary_role"] == "chapter_open"]
        assert len(chapter_open) == 30

    def test_33_plate_verso_blank(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        plate_verso = [
            r for r in records
            if r.get("blank_detail") and r["blank_detail"].get("blank_kind") == "plate_verso_blank"
        ]
        assert len(plate_verso) == 33

    def test_p387_is_appendix_with_table(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        p387 = next(r for r in records if r["physical_page"] == 387)
        assert p387["primary_role"] == "appendix"
        assert "table" in p387["content_features"]

    def test_p400_to_p404_are_appendix(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for pg in range(400, 405):
            r = next(r for r in records if r["physical_page"] == pg)
            assert r["primary_role"] == "appendix", f"p{pg} is {r['primary_role']}"

    def test_p412_is_back_cover(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        p412 = next(r for r in records if r["physical_page"] == 412)
        assert p412["primary_role"] == "back_cover"

    def test_printed_pages_291_to_318(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for pg in range(381, 409):
            r = next(r for r in records if r["physical_page"] == pg)
            expected = pg - 381 + 291
            assert r["printed_page_number"] == expected, f"p{pg} printed={r['printed_page_number']}, expected {expected}"

    def test_override_applied_count(self, project_root):
        _, stats = generate_final_page_map(project_root)
        assert stats["override_applied"] == 153

    def test_manual_classification_source(self, project_root):
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        # p25 should be manual (chapter_open override)
        p25 = next(r for r in records if r["physical_page"] == 25)
        assert p25["classification_source"] == "manual"

    def test_no_absolute_paths(self, project_root):
        path, _ = generate_final_page_map(project_root)
        content = path.read_text("utf-8")
        assert "D:\\" not in content
        assert "C:\\" not in content

    def test_no_api_keys(self, project_root):
        path, _ = generate_final_page_map(project_root)
        content = path.read_text("utf-8")
        assert "Bearer " not in content
        assert "sk-" not in content
        assert "api_key" not in content.lower()

    def test_no_data_urls(self, project_root):
        path, _ = generate_final_page_map(project_root)
        content = path.read_text("utf-8")
        assert "data:image/" not in content
        assert "base64," not in content

    def test_primary_role_not_in_content_features(self, project_root):
        """primary_role should never appear as a content_feature."""
        path, _ = generate_final_page_map(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for r in records:
            role = r["primary_role"]
            cf = r.get("content_features", [])
            # 'map' and 'table' and 'illustration' are valid content features
            # but 'chapter_body', 'appendix', 'blank' etc. should never be content features
            non_cf_roles = {"chapter_body", "chapter_open", "appendix", "blank",
                           "preface", "index", "contents", "cover", "half_title",
                           "frontispiece", "title_page", "digitization_notice",
                           "dedication", "list_of_illustrations", "library_artifact",
                           "back_cover", "unknown"}
            if role in non_cf_roles:
                assert role not in cf, f"p{r['physical_page']}: primary_role '{role}' in content_features"


class TestSectionCandidates:
    def test_generates_sections(self, project_root):
        generate_final_page_map(project_root)
        path = generate_section_candidates(project_root)
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) > 0

    def test_sections_have_valid_fields(self, project_root):
        generate_final_page_map(project_root)
        path = generate_section_candidates(project_root)
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for r in records:
            assert "section_id" in r
            assert "section_type" in r
            assert "start_page" in r
            assert "end_page" in r
            assert r["start_page"] <= r["end_page"]


class TestBookManifest:
    def test_manifest_fields(self, project_root):
        path, stats = generate_final_page_map(project_root)
        manifest_path = generate_book_manifest(project_root, stats)
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text("utf-8"))
        assert manifest["phase"] == "phase_1_final"
        assert manifest["total_pages"] == 412
        assert manifest["manual_audit_applied"] is True
        assert manifest["chapter_open_count"] == 30
        assert manifest["plate_verso_blank_count"] == 33
        assert manifest["api_calls"] == 0
        assert manifest["api_key_logged"] is False
        assert manifest["data_url_persisted"] is False
        assert manifest["boundaries_modified"] is False


class TestGateVerification:
    def test_gate_passes(self, project_root):
        generate_final_page_map(project_root)
        generate_section_candidates(project_root)
        passed, messages = verify_gate(project_root)
        if not passed:
            pytest.fail(f"Gate failed with messages: {messages}")

    def test_frozen_files_unchanged(self, project_root):
        from bookflow.io_utils import sha256_file
        expected = {
            "data/fullbook/structure/registry/page_map.jsonl": "11115a628afb806267fd15f48731550705a938cd3f3d2fc87db82f295db2f5ad",
            "data/fullbook/structure/bridges/bridge_candidates.jsonl": "169a214c10171a5fecaa758d066d541200c05d7c81be3dd7c059c58607ce814a",
            "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl": "b08c4bab8506f6d85cfd5e48b54ec801bd1868e10e6fb1375779011c08faf5a1",
        }
        for rel, expected_sha in expected.items():
            actual = sha256_file(project_root / rel)
            assert actual == expected_sha, f"{rel} SHA changed"
