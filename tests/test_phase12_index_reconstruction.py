from __future__ import annotations

import json
from pathlib import Path

from bookflow.index_reconstruction import build_index_reading_order


ROOT = Path(".")


def test_index_source_coverage_and_validation_gates():
    model = build_index_reading_order(ROOT)
    validation = model["validation"]
    assert validation["valid"] is True
    assert validation["source_page_coverage"] == [405, 406, 407, 408]
    assert validation["source_line_count"] == 314
    assert validation["represented_source_line_count"] == 314
    assert validation["missing_source_elements"] == 0
    assert validation["duplicate_source_mapping_count"] == 0
    assert validation["orphan_node_ids"] == []
    assert validation["unresolved_cross_reference_count"] == 0
    assert validation["reading_order_valid"] is True


def test_index_hierarchy_counts_and_serow_subentries():
    model = build_index_reading_order(ROOT)
    validation = model["validation"]
    assert validation["main_entry_count"] == 193
    assert validation["subentry_count"] == 105
    assert validation["continuation_count"] == 16
    by_id = {node["index_node_id"]: node for node in model["nodes"]}
    serow = by_id["index_node_0212"]
    children = [node for node in model["nodes"] if node["parent_id"] == serow["index_node_id"]]
    assert [node["term"] for node in children] == [
        "appearance of", "driving", "hunting", "numbers of", "stalking",
    ]
    assert all(node["indent_level"] == 1 for node in children)


def test_persisted_index_and_units_are_formal_and_provenanced():
    model = json.loads((ROOT / "data/fullbook/back_matter/index_reading_order_v1.json").read_text("utf-8"))
    units = [
        json.loads(line)
        for line in (ROOT / "data/fullbook/multilingual/units/translation_units_index_zh-Hans_v1.jsonl").read_text("utf-8").splitlines()
        if line
    ]
    assert model["validation"]["valid"] is True
    assert len(units) == model["validation"]["node_count"] == 298
    assert len({unit["translation_unit_id"] for unit in units}) == 298
    assert all(unit["translation_status"] == "pending" for unit in units)
    assert all(unit["source_object_type"] == "reconstructed_index_entry" for unit in units)
    assert all(unit["provenance"]["legacy_candidate_groups_preserved"] == 334 for unit in units)
