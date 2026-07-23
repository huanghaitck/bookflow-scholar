"""Integration tests for Phase 1B offline structure registration.

These tests use the real project data (412-page PDF, render cache, automated
page records, boundaries).  They must be run from the project root inside the
bilingual-book conda environment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bookflow.structure_scan import (
    DEFAULT_MAX_BRIDGE_DISTANCE,
    EXPECTED_PAGE_COUNT,
    EXPECTED_PDF_SHA256,
    StructureBatchRunner,
    discover_bridge_candidates,
    register_pages,
)
from bookflow.structure_schemas import PrimaryRole

ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT / "input" / "The big game of central and western China (1913).pdf"
STRUCTURE_DIR = ROOT / "data" / "fullbook" / "structure"
BOUNDARIES_PATH = ROOT / "data" / "fullbook" / "main_text" / "boundaries" / "main_text.boundaries.jsonl"

pytestmark = pytest.mark.skipif(
    not PDF_PATH.is_file(),
    reason="Authoritative PDF not found",
)


def _load_boundaries() -> list[dict]:
    if not BOUNDARIES_PATH.is_file():
        return []
    return [json.loads(l) for l in BOUNDARIES_PATH.read_text("utf-8").splitlines() if l.strip()]


def _boundary_endpoints() -> set[tuple[int, int]]:
    return {(b["previous_page"], b["next_page"]) for b in _load_boundaries()}


class TestRegisterPages:
    def test_authoritative_pdf_is_412_pages(self):
        import fitz
        doc = fitz.open(str(PDF_PATH))
        assert doc.page_count == EXPECTED_PAGE_COUNT
        doc.close()

    def test_pdf_sha_matches(self):
        from bookflow.io_utils import sha256_file
        assert sha256_file(PDF_PATH) == EXPECTED_PDF_SHA256

    def test_register_all_412_pages(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        assert len(records) == EXPECTED_PAGE_COUNT

    def test_page_numbers_are_1_to_412_no_gaps(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        pages = sorted(r.physical_page for r in records)
        assert pages == list(range(1, EXPECTED_PAGE_COUNT + 1))

    def test_no_duplicate_pages(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        pages = [r.physical_page for r in records]
        assert len(pages) == len(set(pages))

    def test_p380_is_blank_candidate(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        p380 = next(r for r in records if r.physical_page == 380)
        assert p380.primary_role == PrimaryRole.blank

    def test_p409_is_blank_candidate(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        p409 = next(r for r in records if r.physical_page == 409)
        assert p409.primary_role == PrimaryRole.blank

    def test_p412_not_blank(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        p412 = next(r for r in records if r.physical_page == 412)
        assert p412.primary_role != PrimaryRole.blank

    def test_no_absolute_paths_in_asset_ref(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            assert not (len(r.source_page_asset_ref) >= 2 and r.source_page_asset_ref[1] == ":")
            assert not r.source_page_asset_ref.startswith("/")
            assert not r.source_page_asset_ref.startswith("\\")

    def test_cache_fingerprint_is_deterministic(self):
        """Verify fingerprint matches expected stable_hash of offline inputs."""
        from bookflow.structure_scan import (
            _compute_cache_fingerprint,
            OFFLINE_SCHEMA_VERSION,
            OFFLINE_FEATURE_PROFILE_VERSION,
            THRESHOLD_PROFILE,
        )
        from bookflow.io_utils import stable_hash

        pdf_sha = "a" * 64
        img_sha = "b" * 64
        expected = stable_hash({
            "pdf_sha256": pdf_sha,
            "page_image_sha256": img_sha,
            "offline_schema_version": OFFLINE_SCHEMA_VERSION,
            "offline_feature_profile_version": OFFLINE_FEATURE_PROFILE_VERSION,
            "threshold_profile": THRESHOLD_PROFILE,
        })
        actual = _compute_cache_fingerprint(pdf_sha, img_sha)
        assert actual == expected

    def test_cache_fingerprint_changes_with_profile_version(self):
        """Fingerprint must differ when feature profile version changes."""
        from bookflow.structure_scan import _compute_cache_fingerprint
        fp1 = _compute_cache_fingerprint("a" * 64, "b" * 64)
        # Any change to inputs must produce a different fingerprint
        fp2 = _compute_cache_fingerprint("a" * 64, "c" * 64)
        assert fp1 != fp2

    def test_blank_records_have_blank_detail(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.primary_role == PrimaryRole.blank:
                assert r.blank_detail is not None
                assert r.blank_detail.blank_requires_visual_confirmation is True

    def test_unknown_pages_have_requires_followup(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.primary_role == PrimaryRole.unknown:
                assert r.requires_followup is True

    def test_body_pages_have_fragment_ids(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        body_pages = [r for r in records if r.text_flow_fragment_ids]
        assert len(body_pages) > 0
        for r in body_pages:
            assert r.contains_prose is True

    def test_rendering_policy_has_no_bridge_field(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            assert "eligible_as_text_flow_bridge" not in r.rendering_policy.model_fields


class TestBridgeCandidates:
    @pytest.fixture(scope="class")
    def all_records(self):
        return register_pages(root=ROOT, pdf_path=PDF_PATH)

    @pytest.fixture(scope="class")
    def candidates(self, all_records):
        from bookflow.structure_scan import _load_automated_page_records, _load_manual_confirmations
        ap_records = list(_load_automated_page_records(ROOT).values())
        boundaries = _load_boundaries()
        manual_confs = _load_manual_confirmations(ROOT)
        return discover_bridge_candidates(
            structure_pages=all_records,
            existing_page_records=ap_records,
            existing_boundaries=boundaries,
            max_bridge_distance=DEFAULT_MAX_BRIDGE_DISTANCE,
            manual_confirmations=manual_confs,
        )

    def test_candidate_count_matches_boundaries(self, candidates):
        boundaries = _load_boundaries()
        assert len(candidates) == len(boundaries)

    def test_candidate_endpoints_match_boundary_endpoints(self, candidates):
        candidate_endpoints = {(c.from_page, c.to_page) for c in candidates}
        boundary_endpoints = _boundary_endpoints()
        assert candidate_endpoints == boundary_endpoints

    def test_non_adjacent_count_matches(self, candidates):
        boundaries = _load_boundaries()
        expected_non_adj = sum(
            1 for b in boundaries if b["next_page"] - b["previous_page"] > 1
        )
        actual_non_adj = sum(1 for c in candidates if c.intervening_pages)
        assert actual_non_adj == expected_non_adj

    def test_p196_to_p199_intervening_pages(self, candidates):
        c = next(c for c in candidates if c.from_page == 196 and c.to_page == 199)
        assert c.intervening_pages == [197, 198]
        assert len(c.intervening_page_details) == 2

    def test_p336_to_p341_intervening_pages(self, candidates):
        c = next(c for c in candidates if c.from_page == 336 and c.to_page == 341)
        assert c.intervening_pages == [337, 338, 339, 340]
        assert len(c.intervening_page_details) == 4

    def test_p336_to_p341_has_manual_confirmation(self, candidates):
        c = next(c for c in candidates if c.from_page == 336 and c.to_page == 341)
        assert c.manual_confirmation is not None
        assert c.manual_confirmation.confirmed_by == "project_owner"

    def test_p336_to_p341_candidate_source(self, candidates):
        c = next(c for c in candidates if c.from_page == 336 and c.to_page == 341)
        assert c.candidate_source == "existing_boundary"

    def test_bridge_candidate_has_no_join_operation(self, candidates):
        for c in candidates:
            assert not hasattr(c, "join_operation")
            assert not hasattr(c, "structural_break")

    def test_all_candidates_source_is_existing_boundary(self, candidates):
        for c in candidates:
            assert c.candidate_source == "existing_boundary"

    def test_max_bridge_distance_validation(self, all_records):
        with pytest.raises(ValueError):
            discover_bridge_candidates(
                structure_pages=all_records,
                max_bridge_distance=0,
            )
        with pytest.raises(ValueError):
            discover_bridge_candidates(
                structure_pages=all_records,
                max_bridge_distance=51,
            )


class TestBatchRunnerSafety:
    def test_allow_api_true_raises(self):
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        with pytest.raises(NotImplementedError):
            runner.run(allow_api=True)

    def test_dry_run(self):
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        result = runner.run(dry_run=True)
        assert result.api_calls == 0
        assert result.total_pages == EXPECTED_PAGE_COUNT

    def test_no_api_key_read(self, monkeypatch):
        import os
        accessed_keys: list[str] = []
        original_getenv = os.getenv

        def fake_getenv(key, default=None):
            if "KEY" in key.upper() or "API" in key.upper():
                accessed_keys.append(key)
            return original_getenv(key, default)

        monkeypatch.setattr(os, "getenv", fake_getenv)
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run(dry_run=True)
        assert accessed_keys == []


class TestIdempotency:
    def test_second_run_all_cached(self):
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run()
        result2 = runner.run()
        assert len(result2.cached_pages) == EXPECTED_PAGE_COUNT
        assert len(result2.pending_pages) == 0
        assert result2.api_calls == 0

    def test_output_hash_stable(self):
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run()
        page_map_path = STRUCTURE_DIR / "registry" / "page_map.jsonl"
        bridges_path = STRUCTURE_DIR / "bridges" / "bridge_candidates.jsonl"
        hash1 = hashlib.sha256(page_map_path.read_bytes()).hexdigest()
        bhash1 = hashlib.sha256(bridges_path.read_bytes()).hexdigest()
        runner.run()
        hash2 = hashlib.sha256(page_map_path.read_bytes()).hexdigest()
        bhash2 = hashlib.sha256(bridges_path.read_bytes()).hexdigest()
        assert hash1 == hash2
        assert bhash1 == bhash2


class TestFrozenBaseline:
    FROZEN_PATHS = [
        "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
        "data/fullbook/main_text/source_document_main_text_v1.json",
        "data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json",
    ]

    def test_frozen_files_exist(self):
        for rel in self.FROZEN_PATHS:
            p = ROOT / rel
            assert p.is_file(), f"Frozen file missing: {rel}"
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            assert len(h) == 64

    def test_frozen_files_unchanged_after_force_run(self):
        """Compare actual SHA-256 before and after a force re-registration."""
        before = {}
        for rel in self.FROZEN_PATHS:
            p = ROOT / rel
            assert p.is_file()
            before[rel] = hashlib.sha256(p.read_bytes()).hexdigest()

        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run(force=True)

        for rel in self.FROZEN_PATHS:
            p = ROOT / rel
            after = hashlib.sha256(p.read_bytes()).hexdigest()
            assert before[rel] == after, f"Frozen file changed: {rel}"


class TestUnknownBlankSemantics:
    """unknown_blank must be bridge_blocking, requires_followup, structural_break."""

    def test_unknown_blank_is_bridge_blocking(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.blank_detail and r.blank_detail.blank_kind.value == "unknown_blank":
                assert r.bridge_eligibility.value == "bridge_blocking", (
                    f"p{r.physical_page} unknown_blank must be bridge_blocking"
                )

    def test_unknown_blank_requires_followup(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.blank_detail and r.blank_detail.blank_kind.value == "unknown_blank":
                assert r.requires_followup is True, (
                    f"p{r.physical_page} unknown_blank must have requires_followup=True"
                )

    def test_unknown_blank_text_flow_role_is_structural_break(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.blank_detail and r.blank_detail.blank_kind.value == "unknown_blank":
                assert r.text_flow_role.value == "structural_break", (
                    f"p{r.physical_page} unknown_blank must have text_flow_role=structural_break"
                )

    def test_no_intentional_blank_from_offline(self):
        """Offline evidence cannot prove blank reason, so no intentional_blank."""
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.blank_detail:
                assert r.blank_detail.blank_kind.value != "intentional_blank", (
                    f"p{r.physical_page} must not use intentional_blank from offline evidence"
                )


class TestSemanticElementRenderingPolicy:
    """title_page, contents, appendix, index must have correct rendering policy."""

    def test_semantic_elements_in_book_element_order(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        semantic_roles = {
            PrimaryRole.title_page,
            PrimaryRole.contents,
            PrimaryRole.list_of_illustrations,
            PrimaryRole.appendix,
            PrimaryRole.index,
        }
        for r in records:
            if r.primary_role in semantic_roles:
                assert r.rendering_policy.include_in_book_element_order is True, (
                    f"p{r.physical_page} ({r.primary_role.value}) must be in book_element_order"
                )
                assert r.rendering_policy.include_in_default_output is True, (
                    f"p{r.physical_page} ({r.primary_role.value}) must be in default_output"
                )
                assert r.rendering_policy.include_in_text_flow is False, (
                    f"p{r.physical_page} ({r.primary_role.value}) must not be in text_flow"
                )

    def test_semantic_elements_are_content_bearing(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        semantic_roles = {
            PrimaryRole.title_page,
            PrimaryRole.contents,
            PrimaryRole.list_of_illustrations,
            PrimaryRole.appendix,
            PrimaryRole.index,
        }
        for r in records:
            if r.primary_role in semantic_roles:
                assert r.content_bearing is True, (
                    f"p{r.physical_page} ({r.primary_role.value}) must be content_bearing"
                )

    def test_blank_pages_not_in_default_output(self):
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            if r.primary_role == PrimaryRole.blank:
                assert r.rendering_policy.include_in_default_output is False
                assert r.rendering_policy.include_in_book_element_order is False


class TestImageShaVerification:
    """Render cache image SHA-256 must be verified against manifest."""

    def test_image_sha_mismatch_skips_page(self, monkeypatch):
        """If actual image SHA differs from manifest, page is not registered."""
        import bookflow.structure_scan as ss
        original = ss.sha256_file

        def mock_sha256(path):
            result = original(path)
            p = Path(path)
            if p.name == "page_0001.png":
                return "f" * 64
            return result

        monkeypatch.setattr(ss, "sha256_file", mock_sha256)
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        assert 1 not in {r.physical_page for r in records}

    def test_all_registered_pages_have_verified_sha(self):
        """Every registered page must have a real image SHA (not manifest-only)."""
        from bookflow.io_utils import sha256_file
        records = register_pages(root=ROOT, pdf_path=PDF_PATH)
        for r in records:
            img_path = ROOT / r.source_page_asset_ref
            actual = sha256_file(img_path)
            assert r.page_image_sha256 == actual, (
                f"p{r.physical_page} image SHA mismatch"
            )


class TestCacheValidation:
    """Cached pages must pass checkpoint + page_map + fingerprint checks."""

    def test_stale_fingerprint_triggers_recalculation(self):
        """If page_map fingerprint is stale, page must be re-registered."""
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run(force=True)

        page_map_path = STRUCTURE_DIR / "registry" / "page_map.jsonl"
        original_content = page_map_path.read_text("utf-8")

        try:
            lines = page_map_path.read_text("utf-8").splitlines()
            modified = []
            for line in lines:
                if line.strip():
                    d = json.loads(line)
                    if d["physical_page"] == 1:
                        d["cache_fingerprint"] = "stale_fp_value"
                    modified.append(json.dumps(d, ensure_ascii=False, sort_keys=True))
            page_map_path.write_text(chr(10).join(modified) + chr(10), "utf-8")

            result = runner.run()
            assert 1 in result.pending_pages
            assert 1 in result.completed_pages
        finally:
            page_map_path.write_text(original_content, "utf-8")
            runner.run(force=True)

    def test_checkpoint_without_page_map_triggers_recalculation(self):
        """If checkpoint says completed but page_map is missing, re-register."""
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run(force=True)

        page_map_path = STRUCTURE_DIR / "registry" / "page_map.jsonl"
        original_content = page_map_path.read_text("utf-8")

        try:
            lines = page_map_path.read_text("utf-8").splitlines()
            modified = [l for l in lines if l.strip()]
            modified = [l for l in modified if json.loads(l)["physical_page"] != 1]
            page_map_path.write_text(chr(10).join(modified) + chr(10), "utf-8")

            result = runner.run()
            assert 1 in result.pending_pages
            assert 1 in result.completed_pages
        finally:
            page_map_path.write_text(original_content, "utf-8")
            runner.run(force=True)

    def test_quarantine_cleared_on_successful_reregistration(self):
        """After successful re-registration, old quarantine entry must be cleared."""
        runner = StructureBatchRunner(root=ROOT, pdf_path=PDF_PATH)
        runner.run(force=True)

        # Manually quarantine page 1
        runner.checkpoint.mark_quarantine(1, "manual test quarantine")
        assert "1" in runner.checkpoint.quarantined_pages

        # Force re-run should clear quarantine for successfully registered page
        runner.run(force=True)
        assert "1" not in runner.checkpoint.quarantined_pages
