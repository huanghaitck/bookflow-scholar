"""Tests for Phase 6: Canonical Book Document."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.canonical_book import build_canonical_book, verify_gate, AUTHOR


@pytest.fixture
def root() -> Path:
    return Path(".")


class TestCanonicalBook:
    """Tests that build_canonical_book produces correct outputs."""

    def test_build_returns_path_and_stats(self, root: Path, tmp_path: Path):
        path, stats = build_canonical_book(root, output_root=tmp_path, created_at="2026-07-17T00:08:41.919525+00:00")
        assert path.is_file()
        assert stats["total_pages"] == 412
        assert stats["total_chapters"] == 30
        assert stats["total_logical_units"] == 971

    def test_fixed_timestamp_produces_stable_bytes(self, root: Path, tmp_path: Path):
        fixed = "2026-07-17T00:08:41.919525+00:00"
        path, _ = build_canonical_book(root, output_root=tmp_path / "one", created_at=fixed)
        other, _ = build_canonical_book(root, output_root=tmp_path / "two", created_at=fixed)
        assert path.read_bytes() == other.read_bytes()

    def test_formal_frozen_path_rejects_default_overwrite(self, root: Path):
        with pytest.raises(PermissionError):
            build_canonical_book(root, created_at="2026-07-17T00:08:41.919525+00:00")

    def test_canonical_document_exists(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        assert path.is_file()
        data = json.loads(path.read_text("utf-8"))
        assert data["document_id"] == "doc_78137e1bd662e86b"
        assert data["version"] == "1.0"

    def test_manifest_exists(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_manifest_v1.json"
        assert path.is_file()
        data = json.loads(path.read_text("utf-8"))
        assert data["total_pages"] == 412
        assert data["total_chapters"] == 30
        assert data["total_logical_units"] == 971

    def test_validation_report_exists(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_validation_report_v1.json"
        assert path.is_file()
        data = json.loads(path.read_text("utf-8"))
        assert data["validation_passed"] is True

    def test_physical_page_order_has_412(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["physical_page_order"]) == 412
        pages = [p["physical_page"] for p in data["physical_page_order"]]
        assert pages == list(range(1, 413))

    def test_book_element_order_has_content(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["book_element_order"]) > 47

    def test_prose_text_flow_has_971(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["prose_text_flow_order"]) == 971

    def test_logical_units_have_section_mapping(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        for unit in data["logical_units"][:10]:
            assert unit["section_id"] != "", f"Unit {unit['logical_block_id']} has no section"

    def test_no_absolute_paths(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        content = path.read_text("utf-8")
        assert "D:\\" not in content
        assert "C:\\" not in content

    def test_no_secret_leakage(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        content = path.read_text("utf-8").lower()
        assert "api_key" not in content
        assert "authorization" not in content
        assert "bearer" not in content
        assert "data:image" not in content

    def test_frozen_hashes_verified(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_manifest_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert data["frozen_hashes_verified"] is True

    def test_figures_and_maps_included(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["figures"]) >= 34
        assert len(data["maps"]) == 2

    def test_appendices_and_tables_included(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert len(data["appendices"]) == 3
        assert len(data["tables"]) > 0

    def test_checkpoint_exists(self, root: Path):
        path = root / "data/fullbook/checkpoints/phase_6_checkpoint.json"
        assert path.is_file()
        cp = json.loads(path.read_text("utf-8"))
        assert cp["phase"] == "phase_6"
        assert cp["status"] == "completed"
        assert cp["api_calls"] == 0


class TestCanonicalQualityGates:
    """Content-level quality gate tests from the audit."""

    def test_author_is_harold_frank_wallace(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        assert data["metadata"]["author"] == "Harold Frank Wallace"

    def test_metadata_errata_gates_present(self, root: Path):
        report = json.loads((root / "data/fullbook/canonical/canonical_validation_report_v1.json").read_text("utf-8"))
        checks = {c["check"]: c for c in report["checks"]}
        for name in ["chapter_title_authority_gate", "chapter_title_errata_gate", "confirmed_caption_target_gate", "appendix_title_errata_gate"]:
            assert checks[name]["passed"] is True

    def test_chapter_titles_match_toc(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        chapters = [s for s in data["sections"] if s["section_type"] == "chapter"]
        for ch in chapters:
            title = ch.get("title", "")
            assert title != "UNTITLED", f"Chapter at p{ch['start_page']} is untitled"
            assert title.endswith("THE") is False, f"Ch {ch.get('chapter_number')} title ends with THE: {title}"
            assert title.endswith("AT") is False, f"Ch {ch.get('chapter_number')} title ends with AT: {title}"
            assert title.endswith("WE") is False, f"Ch {ch.get('chapter_number')} title ends with WE: {title}"

    def test_chapter_titles_have_printed_ranges(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        chapters = [s for s in data["sections"] if s["section_type"] == "chapter"]
        for ch in chapters:
            assert ch.get("printed_page_start") is not None, f"Ch {ch.get('chapter_number')} missing printed_page_start"
            assert ch.get("printed_page_end") is not None, f"Ch {ch.get('chapter_number')} missing printed_page_end"

    def test_book_element_order_monotonic_by_page(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        be = data["book_element_order"]
        pages = [e["source_pages"][0] for e in be if e.get("source_pages")]
        assert pages == sorted(pages), f"book_element_order not monotonic: {pages[:10]}..."

    def test_no_duplicate_book_elements(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        be = data["book_element_order"]
        ids = [e["element_id"] for e in be]
        assert len(ids) == len(set(ids)), f"Duplicate element IDs: {set(i for i in ids if ids.count(i) > 1)}"

    def test_tables_in_book_element_order(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        be = data["book_element_order"]
        be_types = {e["element_type"] for e in be}
        assert "table" in be_types, "Tables not in book_element_order"

    def test_appendices_in_book_element_order(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        be = data["book_element_order"]
        be_types = {e["element_type"] for e in be}
        assert "appendix" in be_types, "Appendices not in book_element_order"

    def test_appendix_records_have_section_id(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        for app in data["appendices"]:
            assert app.get("section_id"), f"Appendix {app['appendix_id']} missing section_id"

    def test_table_records_have_section_and_appendix_id(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        for tbl in data["tables"]:
            assert tbl.get("section_id"), f"Table {tbl['table_id']} missing section_id"
            assert tbl.get("appendix_id"), f"Table {tbl['table_id']} missing appendix_id"

    def test_index_entries_have_section_id(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        for entry in data["index_entries"][:10]:
            assert entry.get("section_id"), f"Index entry {entry['entry_id']} missing section_id"

    def test_index_entries_not_merged(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        for entry in data["index_entries"]:
            text = entry.get("entry_text", "")
            assert "\n" not in text, f"Entry {entry['entry_id']} has newline: {text[:50]}"

    def test_p409_is_blank_not_library_artifact(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        p409 = next(p for p in data["page_section_membership"] if p["physical_page"] == 409)
        assert p409["section_type"] == "blank", f"p409 is {p409['section_type']}, expected blank"

    def test_p410_is_library_artifact(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        p410 = next(p for p in data["page_section_membership"] if p["physical_page"] == 410)
        assert p410["section_type"] == "library_artifact", f"p410 is {p410['section_type']}, expected library_artifact"

    def test_body_pages_have_printed_numbers(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
        data = json.loads(path.read_text("utf-8"))
        # Check p25=1, p26=2, p379=289
        for pg, expected in [(25, 1), (26, 2), (379, 289)]:
            record = next(p for p in data["physical_page_order"] if p["physical_page"] == pg)
            actual = record.get("printed_page_number")
            assert actual == expected, f"p{pg} printed={actual}, expected {expected}"

    def test_validation_has_content_checks(self, root: Path):
        path = root / "data/fullbook/canonical/canonical_validation_report_v1.json"
        data = json.loads(path.read_text("utf-8"))
        check_names = {c["check"] for c in data["checks"]}
        assert "30_chapter_titles_valid" in check_names
        assert "book_element_order_monotonic" in check_names
        assert "no_duplicate_book_elements" in check_names
        assert "cross_references_valid" in check_names
        assert "author_correct" in check_names
        assert "printed_page_ranges_complete" in check_names
        assert "index_entries_not_merged" in check_names


class TestCanonicalGate:
    """Tests for the Phase 6 gate verification."""

    def test_gate_passes(self, root: Path):
        passed, messages = verify_gate(root)
        assert passed, f"Gate failed: {messages}"



