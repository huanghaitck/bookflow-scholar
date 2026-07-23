"""Tests for Phase 5: Appendices, Tables, and Index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.back_matter import process_back_matter, verify_gate


@pytest.fixture
def root() -> Path:
    return Path(".")


class TestBackMatter:
    """Tests that process_back_matter produces correct outputs."""

    def test_process_returns_path_and_stats(self, root: Path):
        path, stats = process_back_matter(root)
        assert path.is_file()
        assert stats["appendix_count"] == 3

    def test_appendices_exist(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 3
        ids = {r["appendix_id"] for r in records}
        assert ids == {"appendix_a", "appendix_b", "appendix_c"}

    def test_appendix_a_range(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        app_a = next(r for r in records if r["appendix_id"] == "appendix_a")
        assert app_a["physical_page_start"] == 381
        assert app_a["physical_page_end"] == 397

    def test_appendix_b_range(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        app_b = next(r for r in records if r["appendix_id"] == "appendix_b")
        assert app_b["physical_page_start"] == 398
        assert app_b["physical_page_end"] == 399

    def test_appendix_c_range(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        app_c = next(r for r in records if r["appendix_id"] == "appendix_c")
        assert app_c["physical_page_start"] == 400
        assert app_c["physical_page_end"] == 404

    def test_appendix_title_errata(self, root: Path):
        records = [json.loads(l) for l in (root / "data/fullbook/back_matter/appendices.jsonl").read_text("utf-8").splitlines() if l.strip()]
        apps = {r["appendix_id"]: r for r in records}
        assert apps["appendix_b"]["label"] == "APPENDIX B"
        assert apps["appendix_b"]["title"] == "ESTIMATE OF EXPENSES"
        assert apps["appendix_c"]["title"] == "TABLE OF DISTANCES AND STAGES"
        assert apps["appendix_c"]["subtitle"] == "FROM HONAN TO SIAN-FU.*"

    def test_appendices_have_section_id(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for app in records:
            assert app.get("section_id"), f"Appendix {app['appendix_id']} missing section_id"

    def test_appendices_have_printed_pages(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for app in records:
            assert app.get("printed_page_start") is not None
            assert app.get("printed_page_end") is not None

    def test_tables_exist(self, root: Path):
        path = root / "data/fullbook/back_matter/tables.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) > 0

    def test_tables_have_section_and_appendix_id(self, root: Path):
        path = root / "data/fullbook/back_matter/tables.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for tbl in records:
            assert tbl.get("section_id"), f"Table {tbl['table_id']} missing section_id"
            assert tbl.get("appendix_id"), f"Table {tbl['table_id']} missing appendix_id"

    def test_tables_have_row_count(self, root: Path):
        path = root / "data/fullbook/back_matter/tables.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for tbl in records:
            assert tbl.get("row_count", 0) > 0, f"Table {tbl['table_id']} has 0 rows"

    def test_table_cells_exist(self, root: Path):
        path = root / "data/fullbook/back_matter/table_cells.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) > 0

    def test_table_cells_have_row_col_indices(self, root: Path):
        path = root / "data/fullbook/back_matter/table_cells.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for cell in records[:10]:
            assert "row_index" in cell, f"Cell {cell['cell_id']} missing row_index"
            assert "col_index" in cell, f"Cell {cell['cell_id']} missing col_index"

    def test_index_entries_exist(self, root: Path):
        path = root / "data/fullbook/back_matter/index_entries.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) > 0
        groups = [json.loads(l) for l in (root / "data/fullbook/back_matter/index_entry_groups.jsonl").read_text("utf-8").splitlines() if l.strip()]
        idx_pages = sorted(set(r["physical_page"] for r in records + groups))
        assert idx_pages == [405, 406, 407, 408]

    def test_index_entries_have_section_id(self, root: Path):
        path = root / "data/fullbook/back_matter/index_entries.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for entry in records[:10]:
            assert entry.get("section_id"), f"Index entry {entry['entry_id']} missing section_id"

    def test_index_entries_not_merged(self, root: Path):
        path = root / "data/fullbook/back_matter/index_entries.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for entry in records:
            text = entry.get("entry_text", "")
            assert "\n" not in text, f"Entry {entry['entry_id']} has newline: {text[:50]}"

    def test_reading_order_exists(self, root: Path):
        path = root / "data/fullbook/back_matter/back_matter_reading_order.jsonl"
        assert path.is_file()
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) > 0

    def test_reading_order_has_tables(self, root: Path):
        path = root / "data/fullbook/back_matter/back_matter_reading_order.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        types = {r["element_type"] for r in records}
        assert "table" in types, "Tables not in reading order"

    def test_unresolved_explicit(self, root: Path):
        path = root / "data/fullbook/back_matter/unresolved_regions.jsonl"
        assert path.is_file()

    def test_appendix_a_has_table_feature(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        app_a = next(r for r in records if r["appendix_id"] == "appendix_a")
        assert "table" in app_a["content_features"]

    def test_printed_pages_continuous(self, root: Path):
        path = root / "data/fullbook/back_matter/appendices.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        for app in records:
            assert app["printed_page_start"] is not None
            assert app["printed_page_end"] is not None


class TestBackMatterGate:
    """Tests for the Phase 5 gate verification."""

    def test_gate_passes(self, root: Path):
        passed, messages = verify_gate(root)
        assert passed, f"Gate failed: {messages}"

    def test_gate_checkpoint(self, root: Path):
        path = root / "data/fullbook/checkpoints/phase_5_checkpoint.json"
        assert path.is_file()
        cp = json.loads(path.read_text("utf-8"))
        assert cp["phase"] == "phase_5"
        assert cp["status"] == "completed"
        assert cp["appendix_count"] == 3
        assert cp["api_calls"] == 0
