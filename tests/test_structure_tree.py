"""Tests for Phase 2: Book Structure Tree."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.structure_tree import (
    CHAPTER_OPEN_PAGES,
    build_structure_tree,
    verify_gate,
)


@pytest.fixture
def root() -> Path:
    return Path(".")


class TestStructureTreeBuild:
    """Tests that build_structure_tree produces correct outputs."""

    def test_build_returns_path_and_stats(self, root: Path):
        path, stats = build_structure_tree(root)
        assert path.is_file()
        assert stats["total_pages"] == 412
        assert stats["chapter_count"] == 30

    def test_book_structure_json_exists(self, root: Path):
        path = root / "data/fullbook/structure/tree/book_structure.json"
        assert path.is_file()
        data = json.loads(path.read_text("utf-8"))
        assert data["total_pages"] == 412
        assert data["document_id"] == "doc_78137e1bd662e86b"

    def test_sections_jsonl_has_correct_count(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        # 10 front matter + 30 chapters + 7 back matter = 47
        assert len(records) == 48  # 10 front matter + 30 chapters + 8 back matter (p409 blank split)

    def test_page_membership_has_412_records(self, root: Path):
        path = root / "data/fullbook/structure/tree/page_section_membership.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 412
        pages = [r["physical_page"] for r in records]
        assert pages == list(range(1, 413))

    def test_printed_page_map_has_412_records(self, root: Path):
        path = root / "data/fullbook/structure/tree/printed_page_map.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 412


class TestStructureTreeGate:
    """Tests for the Phase 2 gate verification."""

    def test_gate_passes(self, root: Path):
        passed, messages = verify_gate(root)
        assert passed, f"Gate failed: {messages}"

    def test_gate_30_chapters(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapters = [r for r in records if r["section_type"] == "chapter"]
        assert len(chapters) == 30

    def test_gate_chapter_open_starts_chapter(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapters = [r for r in records if r["section_type"] == "chapter"]
        for idx, ch in enumerate(chapters):
            assert ch["start_page"] == CHAPTER_OPEN_PAGES[idx], (
                f"Chapter {idx+1} starts at p{ch['start_page']}, expected p{CHAPTER_OPEN_PAGES[idx]}"
            )

    def test_gate_chapter_ranges_non_overlapping(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapters = [r for r in records if r["section_type"] == "chapter"]
        for i in range(len(chapters) - 1):
            assert chapters[i]["end_page"] < chapters[i + 1]["start_page"], (
                f"Chapter {i+1} overlaps chapter {i+2}"
            )

    def test_gate_appendix_abc_distinct(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for app_type in ["appendix_a", "appendix_b", "appendix_c"]:
            app_sections = [r for r in records if r["section_type"] == app_type]
            assert len(app_sections) == 1, f"Expected 1 {app_type}, got {len(app_sections)}"

    def test_gate_index_pages_405_408(self, root: Path):
        path = root / "data/fullbook/structure/tree/page_section_membership.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        index_pages = sorted(r["physical_page"] for r in records if r["section_type"] == "index")
        assert index_pages == [405, 406, 407, 408]

    def test_gate_p412_back_cover(self, root: Path):
        path = root / "data/fullbook/structure/tree/page_section_membership.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        p412 = next(r for r in records if r["physical_page"] == 412)
        assert p412["section_type"] == "back_cover"

    def test_gate_printed_pages_291_318(self, root: Path):
        path = root / "data/fullbook/structure/tree/printed_page_map.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for pg in range(381, 409):
            r = next(rec for rec in records if rec["physical_page"] == pg)
            expected = pg - 381 + 291
            assert r["printed_page_number"] == expected, (
                f"p{pg} printed={r['printed_page_number']}, expected {expected}"
            )


    def test_gate_body_pages_have_printed_numbers(self, root: Path):
        path = root / "data/fullbook/structure/tree/printed_page_map.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for pg, expected in [(25, 1), (26, 2), (33, 9), (379, 289)]:
            r = next(rec for rec in records if rec["physical_page"] == pg)
            # expected values from actual printed page labels
            assert r["printed_page_number"] == expected, (
                f"p{pg} printed={r['printed_page_number']}, expected {expected}"
            )

    def test_gate_p409_is_blank_section(self, root: Path):
        path = root / "data/fullbook/structure/tree/page_section_membership.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        p409 = next(r for r in records if r["physical_page"] == 409)
        assert p409["section_type"] == "blank", f"p409 is {p409['section_type']}, expected blank"

    def test_gate_p410_is_library_artifact(self, root: Path):
        path = root / "data/fullbook/structure/tree/page_section_membership.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        p410 = next(r for r in records if r["physical_page"] == 410)
        assert p410["section_type"] == "library_artifact", f"p410 is {p410['section_type']}, expected library_artifact"

    def test_chapter_titles_from_chapter_open_visual(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapters = [r for r in records if r["section_type"] == "chapter"]
        for ch in chapters:
            title = ch.get("title", "")
            assert not title.endswith("THE"), f"Ch {ch['ordinal']} title ends with THE: {title}"
            assert not title.endswith("AT"), f"Ch {ch['ordinal']} title ends with AT: {title}"
            assert "bedfbrdi" not in title.lower(), f"Ch {ch['ordinal']} has OCR garbage: {title}"
            assert ch.get("title_source") == "chapter_open_visual"
            assert ch.get("canonical_title")
            assert ch.get("display_title")
            assert ch.get("chapter_open_candidate")
            assert ch.get("toc_candidate")
            assert "title_variants" in ch
            assert ch.get("title_evidence")
            assert "title_conflict" in ch

    def test_verified_title_errata(self, root: Path):
        records = [json.loads(l) for l in (root / "data/fullbook/structure/tree/sections.jsonl").read_text("utf-8").splitlines() if l.strip()]
        chapters = {r["chapter_number"]: r for r in records if r["section_type"] == "chapter"}
        assert chapters[18]["canonical_title"] == "THE WHITE-MANED SEROW (Nemorhædus argyrochaetes)"
        assert "SOME ACCOUNT OF" in chapters[26]["canonical_title"]
        content = json.dumps(chapters, ensure_ascii=False)
        assert "Nemorhcedus argyrochcetes" not in content
        assert "SOME ACCOUNT ON PRZEWALSKI'S GAZELLE" not in content

    def test_chapter_printed_ranges_complete(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapters = [r for r in records if r["section_type"] == "chapter"]
        for ch in chapters:
            assert ch.get("printed_page_start") is not None, f"Ch {ch['ordinal']} missing printed_page_start"
            assert ch.get("printed_page_end") is not None, f"Ch {ch['ordinal']} missing printed_page_end"

    def test_gate_frozen_hashes_unchanged(self, root: Path):
        from bookflow.structure_tree import _verify_frozen_hashes
        results = _verify_frozen_hashes(root)
        for name, ok in results.items():
            assert ok, f"Frozen file {name} hash changed"

    def test_gate_checkpoint_exists(self, root: Path):
        path = root / "data/fullbook/checkpoints/phase_2_checkpoint.json"
        assert path.is_file()
        cp = json.loads(path.read_text("utf-8"))
        assert cp["phase"] == "phase_2"
        assert cp["status"] == "completed"
        assert cp["api_calls"] == 0
        assert cp["frozen_hashes_verified"] is True


class TestChapterTitles:
    """Tests that chapter titles were extracted correctly."""

    def test_all_chapters_have_titles(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        chapters = [r for r in records if r["section_type"] == "chapter"]
        for ch in chapters:
            assert ch["title"] != "UNTITLED", f"Chapter at p{ch['start_page']} is untitled"

    def test_chapter_1_title(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        ch1 = next(r for r in records if r["section_type"] == "chapter" and r["ordinal"] == 1)
        assert "CALL" in ch1["title"].upper()
        assert "RED GODS" in ch1["title"].upper()

    def test_chapter_30_title(self, root: Path):
        path = root / "data/fullbook/structure/tree/sections.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        ch30 = next(r for r in records if r["section_type"] == "chapter" and r["ordinal"] == 30)
        assert "ECHO" in ch30["title"].upper()
        assert "CALL" in ch30["title"].upper()



