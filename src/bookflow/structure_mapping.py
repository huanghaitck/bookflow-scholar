"""Phase 3: Map 971 logical units to the structure tree.

Reads:
- data/fullbook/main_text/source_document_main_text_v1.json (971 entries)
- data/fullbook/structure/tree/sections.jsonl (Phase 2 output)
- data/fullbook/structure/tree/page_section_membership.jsonl (Phase 2 output)
- data/fullbook/main_text/boundaries/main_text.boundaries.jsonl (298 boundaries)

Produces:
- data/fullbook/structure/mapping/logical_unit_section_map.jsonl
- data/fullbook/structure/mapping/section_logical_unit_order.jsonl
- data/fullbook/structure/mapping/unmapped_or_ambiguous_units.jsonl
- data/fullbook/checkpoints/phase_3_checkpoint.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file

SOURCE_DOC_PATH = "data/fullbook/main_text/source_document_main_text_v1.json"
SECTIONS_PATH = "data/fullbook/structure/tree/sections.jsonl"
MEMBERSHIP_PATH = "data/fullbook/structure/tree/page_section_membership.jsonl"
BOUNDARIES_PATH = "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl"
MAPPING_DIR = "data/fullbook/structure/mapping"
CHECKPOINT_DIR = "data/fullbook/checkpoints"

FROZEN_HASHES = {
    "boundaries": ("data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
                   "b08c4bab8506f6d85cfd5e48b54ec801bd1868e10e6fb1375779011c08faf5a1"),
    "source_document": ("data/fullbook/main_text/source_document_main_text_v1.json",
                        "f18ad3eefa24eec1241dbe69a8baa0ecd8512a998a4796b5636c8f980cc01c8a"),
    "bilingual_document": ("data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json",
                           "d00f42fecbd0f8410019a2b9cfb42eeba0a2dc6f97188fdcb00cce8acf4bdc8b"),
}


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _build_page_to_section(memberships: list[dict]) -> dict[int, str]:
    return {m["physical_page"]: m["section_id"] for m in memberships}


def _find_boundary_ids_for_entry(
    entry: dict, boundaries: list[dict]
) -> list[str]:
    """Find boundary IDs that reference this entry's fragments."""
    fragment_ids = set(entry.get("source_fragment_ids", []))
    if not fragment_ids:
        return []
    result: list[str] = []
    for b in boundaries:
        if b.get("previous_fragment_id") in fragment_ids or b.get("next_fragment_id") in fragment_ids:
            result.append(b["boundary_id"])
    return result


def map_logical_units(root: Path) -> tuple[Path, dict]:
    """Map all 971 logical units to sections and write outputs."""
    # Load inputs
    source_doc = json.loads((root / SOURCE_DOC_PATH).read_text("utf-8"))
    entries = source_doc["entries"]
    sections = _load_jsonl(root / SECTIONS_PATH)
    memberships = _load_jsonl(root / MEMBERSHIP_PATH)
    boundaries = _load_jsonl(root / BOUNDARIES_PATH)

    page_to_section = _build_page_to_section(memberships)
    section_lookup = {s["section_id"]: s for s in sections}

    # Map each entry to a section
    unit_maps: list[dict] = []
    quarantined: list[dict] = []
    section_units: dict[str, list[dict]] = {s["section_id"]: [] for s in sections}

    for idx, entry in enumerate(entries):
        unit_id = entry["logical_block_id"]
        pages = entry.get("source_pages", [])
        chapter_id = entry.get("chapter_id")

        # Find which section(s) this entry's pages fall into
        sec_ids = set(page_to_section.get(p) for p in pages if p in page_to_section)

        if len(sec_ids) == 0:
            quarantined.append({
                "logical_block_id": unit_id,
                "source_pages": pages,
                "chapter_id": chapter_id,
                "reason": "no_section_found",
                "block_type": entry.get("block_type"),
                "source_text_preview": entry.get("source_text", "")[:100],
            })
            continue

        if len(sec_ids) > 1:
            quarantined.append({
                "logical_block_id": unit_id,
                "source_pages": pages,
                "chapter_id": chapter_id,
                "reason": "crosses_section_boundary",
                "sections": sorted(sec_ids),
                "block_type": entry.get("block_type"),
                "source_text_preview": entry.get("source_text", "")[:100],
            })
            continue

        sec_id = sec_ids.pop()
        sec = section_lookup[sec_id]

        # Find associated boundary IDs
        boundary_ids = _find_boundary_ids_for_entry(entry, boundaries)

        unit_map = {
            "logical_block_id": unit_id,
            "section_id": sec_id,
            "section_type": sec["section_type"],
            "source_pages": pages,
            "chapter_id": chapter_id,
            "block_type": entry.get("block_type"),
            "cross_page": entry.get("cross_page", False),
            "boundary_ids": boundary_ids,
            "mapping_source": "source_pages",
            "mapping_confidence": 1.0,
            "status": "mapped",
            "notes": "",
        }
        unit_maps.append(unit_map)
        section_units[sec_id].append(unit_map)

    # Build section_logical_unit_order
    section_order: list[dict] = []
    for section in sections:
        sec_id = section["section_id"]
        units = section_units.get(sec_id, [])
        # Sort by first source page, then by original entry order (preserved by idx)
        units_sorted = sorted(units, key=lambda u: (
            min(u["source_pages"]) if u["source_pages"] else 999,
        ))
        for ord_idx, unit in enumerate(units_sorted):
            unit["ordinal_in_section"] = ord_idx + 1

        section_order.append({
            "section_id": sec_id,
            "section_type": section["section_type"],
            "title": section.get("title", ""),
            "start_page": section["start_page"],
            "end_page": section["end_page"],
            "unit_count": len(units_sorted),
            "units": [
                {
                    "logical_block_id": u["logical_block_id"],
                    "ordinal_in_section": u["ordinal_in_section"],
                    "source_pages": u["source_pages"],
                    "block_type": u["block_type"],
                }
                for u in units_sorted
            ],
        })

    # Write outputs
    out_dir = root / MAPPING_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    map_path = out_dir / "logical_unit_section_map.jsonl"
    atomic_write_jsonl(map_path, unit_maps)

    order_path = out_dir / "section_logical_unit_order.jsonl"
    atomic_write_jsonl(order_path, section_order)

    quarantine_path = out_dir / "unmapped_or_ambiguous_units.jsonl"
    atomic_write_jsonl(quarantine_path, quarantined)

    # Verify frozen hashes
    frozen_status = {}
    for name, (rel, expected) in FROZEN_HASHES.items():
        path = root / rel
        if path.is_file():
            frozen_status[name] = sha256_file(path) == expected
        else:
            frozen_status[name] = False

    # Write checkpoint
    cp_dir = root / CHECKPOINT_DIR
    cp_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "phase": "phase_3",
        "status": "completed",
        "timestamp": _iso_now(),
        "total_units": len(entries),
        "mapped_units": len(unit_maps),
        "quarantined_units": len(quarantined),
        "sections_with_units": sum(1 for s in section_order if s["unit_count"] > 0),
        "outputs": {
            "logical_unit_section_map": str(map_path),
            "section_logical_unit_order": str(order_path),
            "unmapped_or_ambiguous_units": str(quarantine_path),
        },
        "output_hashes": {
            "logical_unit_section_map": sha256_file(map_path),
            "section_logical_unit_order": sha256_file(order_path),
            "unmapped_or_ambiguous_units": sha256_file(quarantine_path),
        },
        "frozen_hashes_verified": all(frozen_status.values()),
        "frozen_hash_details": frozen_status,
        "api_calls": 0,
    }
    cp_path = cp_dir / "phase_3_checkpoint.json"
    atomic_write_json(cp_path, checkpoint)

    stats = {
        "total_units": len(entries),
        "mapped_units": len(unit_maps),
        "quarantined_units": len(quarantined),
        "sections_with_units": sum(1 for s in section_order if s["unit_count"] > 0),
        "frozen_hashes_verified": all(frozen_status.values()),
    }
    return map_path, stats


def verify_gate(root: Path) -> tuple[bool, list[str]]:
    """Run all Phase 3 gate checks."""
    messages: list[str] = []

    map_path = root / MAPPING_DIR / "logical_unit_section_map.jsonl"
    order_path = root / MAPPING_DIR / "section_logical_unit_order.jsonl"
    quarantine_path = root / MAPPING_DIR / "unmapped_or_ambiguous_units.jsonl"

    if not map_path.is_file():
        return False, ["logical_unit_section_map.jsonl not found"]

    unit_maps = _load_jsonl(map_path)
    section_order = _load_jsonl(order_path)
    quarantined = _load_jsonl(quarantine_path)

    # 1. Exactly 971 units accounted for
    total = len(unit_maps) + len(quarantined)
    if total != 971:
        messages.append(f"Expected 971 total units, got {total} (mapped={len(unit_maps)}, quarantined={len(quarantined)})")

    # 2. No duplicate unit IDs
    unit_ids = [u["logical_block_id"] for u in unit_maps]
    unit_ids.extend(q["logical_block_id"] for q in quarantined)
    if len(unit_ids) != len(set(unit_ids)):
        messages.append("Duplicate logical_block_id found")

    # 3. Each mapped unit has a valid section_id
    sections = _load_jsonl(root / SECTIONS_PATH)
    valid_section_ids = {s["section_id"] for s in sections}
    for u in unit_maps:
        if u["section_id"] not in valid_section_ids:
            messages.append(f"Unit {u['logical_block_id']} has invalid section_id: {u['section_id']}")

    # 4. Target zero unresolved
    if len(quarantined) > 0:
        messages.append(f"Expected 0 quarantined units, got {len(quarantined)}")

    # 5. Frozen hashes unchanged
    for name, (rel, expected) in FROZEN_HASHES.items():
        path = root / rel
        if path.is_file():
            actual = sha256_file(path)
            if actual != expected:
                messages.append(f"Frozen file {name} hash changed")
        else:
            messages.append(f"Frozen file {name} not found")

    return len(messages) == 0, messages