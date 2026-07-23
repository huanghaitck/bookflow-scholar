"""Tests for Phase 3: Logical Unit Mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookflow.structure_mapping import map_logical_units, verify_gate


@pytest.fixture
def root() -> Path:
    return Path(".")


class TestMapping:
    """Tests that map_logical_units produces correct outputs."""

    def test_map_returns_path_and_stats(self, root: Path):
        path, stats = map_logical_units(root)
        assert path.is_file()
        assert stats["total_units"] == 971
        assert stats["mapped_units"] == 971
        assert stats["quarantined_units"] == 0

    def test_unit_map_has_971_records(self, root: Path):
        path = root / "data/fullbook/structure/mapping/logical_unit_section_map.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 971

    def test_no_quarantined_units(self, root: Path):
        path = root / "data/fullbook/structure/mapping/unmapped_or_ambiguous_units.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 0

    def test_section_order_has_47_sections(self, root: Path):
        path = root / "data/fullbook/structure/mapping/section_logical_unit_order.jsonl"
        records = [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]
        assert len(records) == 48

    def test_all_units_have_valid_section(self, root: Path):
        sections_path = root / "data/fullbook/structure/tree/sections.jsonl"
        sections = [json.loads(l) for l in sections_path.read_text("utf-8").splitlines() if l.strip()]
        valid_ids = {s["section_id"] for s in sections}
        map_path = root / "data/fullbook/structure/mapping/logical_unit_section_map.jsonl"
        records = [json.loads(l) for l in map_path.read_text("utf-8").splitlines() if l.strip()]
        for r in records:
            assert r["section_id"] in valid_ids, f"Invalid section_id for {r['logical_block_id']}"

    def test_no_duplicate_unit_ids(self, root: Path):
        map_path = root / "data/fullbook/structure/mapping/logical_unit_section_map.jsonl"
        records = [json.loads(l) for l in map_path.read_text("utf-8").splitlines() if l.strip()]
        ids = [r["logical_block_id"] for r in records]
        assert len(ids) == len(set(ids)), "Duplicate logical_block_id found"

    def test_chapter_1_has_correct_units(self, root: Path):
        order_path = root / "data/fullbook/structure/mapping/section_logical_unit_order.jsonl"
        records = [json.loads(l) for l in order_path.read_text("utf-8").splitlines() if l.strip()]
        ch1 = next(r for r in records if r["section_id"] == "ch_01")
        assert ch1["unit_count"] == 16  # From source data: 16 entries for chapter I
        assert ch1["start_page"] == 25

    def test_each_section_has_ordinals(self, root: Path):
        order_path = root / "data/fullbook/structure/mapping/section_logical_unit_order.jsonl"
        records = [json.loads(l) for l in order_path.read_text("utf-8").splitlines() if l.strip()]
        for section in records:
            if section["unit_count"] > 0:
                ordinals = [u["ordinal_in_section"] for u in section["units"]]
                assert ordinals == list(range(1, section["unit_count"] + 1)), (
                    f"Section {section['section_id']} ordinals not sequential"
                )


class TestMappingGate:
    """Tests for the Phase 3 gate verification."""

    def test_gate_passes(self, root: Path):
        passed, messages = verify_gate(root)
        assert passed, f"Gate failed: {messages}"

    def test_gate_971_accounted(self, root: Path):
        map_path = root / "data/fullbook/structure/mapping/logical_unit_section_map.jsonl"
        q_path = root / "data/fullbook/structure/mapping/unmapped_or_ambiguous_units.jsonl"
        mapped = len([l for l in map_path.read_text("utf-8").splitlines() if l.strip()])
        quarantined = len([l for l in q_path.read_text("utf-8").splitlines() if l.strip()])
        assert mapped + quarantined == 971

    def test_gate_frozen_hashes(self, root: Path):
        from bookflow.structure_mapping import FROZEN_HASHES
        from bookflow.io_utils import sha256_file
        root = Path(".")
        for name, (rel, expected) in FROZEN_HASHES.items():
            actual = sha256_file(root / rel)
            assert actual == expected, f"Frozen file {name} hash changed"

    def test_gate_checkpoint(self, root: Path):
        path = root / "data/fullbook/checkpoints/phase_3_checkpoint.json"
        assert path.is_file()
        cp = json.loads(path.read_text("utf-8"))
        assert cp["phase"] == "phase_3"
        assert cp["status"] == "completed"
        assert cp["mapped_units"] == 971
        assert cp["quarantined_units"] == 0
        assert cp["api_calls"] == 0
