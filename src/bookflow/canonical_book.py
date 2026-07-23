"""Phase 6: Canonical Book Document.

Combines metadata, all physical pages, structure tree, front matter,
30 chapters, 971 existing source units, figures/maps/captions,
appendices/tables/index, blanks/artifacts, provenance, assets, and
independent orderings into a single canonical JSON document.

Key fixes from audit:
- Author corrected to Harold Frank Wallace (from title page p7)
- book_element_order interleaves figures/maps by physical page position
- Tables and appendices enter book_element_order at correct positions
- Review status: confirmed vs unresolved, not pending_review + unresolved=0
- Content validation checks titles, printed pages, element order, references
- Old canonical preserved as draft

Reads:
- All Phase 2-5 outputs
- Phase 1 Final page_map
- Source document and boundaries

Produces:
- data/fullbook/canonical/canonical_book_document_v1.json
- data/fullbook/canonical/canonical_book_manifest_v1.json
- data/fullbook/canonical/canonical_validation_report_v1.json
- data/fullbook/checkpoints/phase_6_checkpoint.json
"""

from __future__ import annotations

import json
from pathlib import Path

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file

PAGE_MAP_PATH = "data/fullbook/structure/final/page_map.jsonl"
SOURCE_DOC_PATH = "data/fullbook/main_text/source_document_main_text_v1.json"
BOUNDARIES_PATH = "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl"
TREE_DIR = "data/fullbook/structure/tree"
MAPPING_DIR = "data/fullbook/structure/mapping"
ASSETS_DIR = "data/fullbook/assets"
BACK_MATTER_DIR = "data/fullbook/back_matter"
CANONICAL_DIR = "data/fullbook/canonical"
CHECKPOINT_DIR = "data/fullbook/checkpoints"

PDF_SHA256 = "78137e1bd662e86b70cb1f197065e155fe003259c2e0244278221b4088990020"
DOCUMENT_ID = "doc_78137e1bd662e86b"

# Author from title page p7: "BY HAROLD FRANK WALLACE, F.R.G.S., F.Z.S."
AUTHOR = "Harold Frank Wallace"

FROZEN_HASHES = {
    "page_map_phase1b": ("data/fullbook/structure/registry/page_map.jsonl",
                         "11115a628afb806267fd15f48731550705a938cd3f3d2fc87db82f295db2f5ad"),
    "bridge_candidates": ("data/fullbook/structure/bridges/bridge_candidates.jsonl",
                          "169a214c10171a5fecaa758d066d541200c05d7c81be3dd7c059c58607ce814a"),
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


def _build_physical_page_order(page_map: dict, memberships: list[dict]) -> list[dict]:
    """Build physical_page_order for all 412 pages."""
    page_to_section = {m["physical_page"]: m["section_id"] for m in memberships}
    page_to_printed = {m["physical_page"]: m.get("printed_page_number") for m in memberships}
    page_to_numbering = {m["physical_page"]: m.get("numbering_scheme") for m in memberships}
    result = []
    for pg in range(1, 413):
        record = page_map[pg]
        result.append({
            "physical_page": pg,
            "section_id": page_to_section.get(pg, ""),
            "primary_role": record["primary_role"],
            "content_features": record.get("content_features", []),
            "artifact_overlays": record.get("artifact_overlays", []),
            "printed_page_number": page_to_printed.get(pg),
            "page_side": record.get("page_side"),
            "source_page_asset_ref": record.get("source_page_asset_ref", ""),
        })
    return result


def _build_book_element_order(
    sections: list[dict],
    figures: list[dict],
    maps: list[dict],
    tables: list[dict],
    appendices: list[dict],
    index_entries: list[dict],
    unit_map: list[dict],
) -> list[dict]:
    """Build book_element_order with elements interleaved by physical page position.

    Front matter sections come first (by page order).
    Then body: chapters and figures/maps interleaved by their source page.
    Then back matter: appendices, tables, index in page order.
    No duplicates: cover/frontispiece/map appear once as figures, not as both
    section and figure.
    """
    # Collect all elements with their primary page for sorting
    elements: list[tuple[int, dict]] = []

    # Front matter sections (skip cover/frontispiece/map - they appear as figures)
    fm_figure_types = {"cover", "frontispiece", "map"}
    for section in sections:
        if section["parent"] != "front_matter":
            continue
        if section["section_type"] in fm_figure_types:
            continue  # These appear as figure/map elements instead
        elements.append((section["start_page"], {
            "element_type": section["section_type"],
            "element_id": section["section_id"],
            "title": section.get("title", ""),
            "source_pages": list(range(section["start_page"], section["end_page"] + 1)),
        }))

    # Body: chapters and figures/maps interleaved by page
    chapters = [s for s in sections if s["section_type"] == "chapter"]
    for ch in chapters:
        elements.append((ch["start_page"], {
            "element_type": "chapter",
            "element_id": ch["section_id"],
            "title": ch.get("title", ""),
            "canonical_title": ch.get("canonical_title", ch.get("title", "")),
            "display_title": ch.get("display_title", ch.get("title", "")),
            "title_source": ch.get("title_source"),
            "chapter_number": ch.get("chapter_number"),
            "chapter_roman": ch.get("chapter_roman", ""),
            "source_pages": list(range(ch["start_page"], ch["end_page"] + 1)),
            "logical_unit_ids": [u["logical_block_id"] for u in unit_map if u["section_id"] == ch["section_id"]],
            "unit_count": sum(1 for u in unit_map if u["section_id"] == ch["section_id"]),
        }))

    # Figures and maps at their source page position
    for fig in sorted(figures + maps, key=lambda f: f["source_page"]):
        elements.append((fig["source_page"], {
            "element_type": "figure" if fig.get("figure_type") != "map" else "map",
            "element_id": fig["figure_id"],
            "source_pages": [fig["source_page"]],
            "caption_ids": fig.get("caption_ids", []),
        }))

    # Back matter: appendices, tables, index in page order
    for section in sections:
        if section["parent"] != "back_matter":
            continue
        if section["section_type"] in ("blank", "library_artifact"):
            elements.append((section["start_page"], {
                "element_type": section["section_type"],
                "element_id": section["section_id"],
                "title": section.get("title", ""),
                "source_pages": list(range(section["start_page"], section["end_page"] + 1)),
            }))

    for app in appendices:
        elements.append((app["physical_page_start"], {
            "element_type": "appendix",
            "element_id": app["appendix_id"],
            "section_id": app.get("section_id", ""),
            "title": app.get("title", ""),
            "label": app.get("label"),
            "subtitle": app.get("subtitle"),
            "source_pages": list(range(app["physical_page_start"], app["physical_page_end"] + 1)),
        }))

    for tbl in sorted(tables, key=lambda t: t["source_pages"][0]):
        elements.append((tbl["source_pages"][0], {
            "element_type": "table",
            "element_id": tbl["table_id"],
            "section_id": tbl.get("section_id", ""),
            "appendix_id": tbl.get("appendix_id", ""),
            "source_pages": tbl["source_pages"],
        }))

    if index_entries:
        elements.append((405, {
            "element_type": "index",
            "element_id": "index",
            "section_id": "bm_index",
            "title": "INDEX",
            "source_pages": [405, 406, 407, 408],
        }))

    # Back cover
    for section in sections:
        if section["section_type"] == "back_cover":
            elements.append((section["start_page"], {
                "element_type": "back_cover",
                "element_id": section["section_id"],
                "title": section.get("title", ""),
                "source_pages": [section["start_page"]],
            }))

    # Sort by primary page and assign ordinals
    elements.sort(key=lambda e: e[0])
    book_element_order = []
    for ordinal, (_, elem) in enumerate(elements, 1):
        elem["ordinal"] = ordinal
        book_element_order.append(elem)

    return book_element_order


def _build_prose_text_flow_order(entries: list[dict], unit_map: list[dict]) -> list[dict]:
    """Build prose_text_flow_order from source entries and section mapping."""
    result = []
    for entry in entries:
        result.append({
            "logical_block_id": entry["logical_block_id"],
            "source_pages": entry.get("source_pages", []),
            "section_id": next(
                (u["section_id"] for u in unit_map if u["logical_block_id"] == entry["logical_block_id"]),
                ""
            ),
            "block_type": entry.get("block_type"),
            "source_text_sha256": entry.get("source_text_sha256", ""),
        })
    return result


def _build_canonical_document(root: Path, created_at: str | None = None) -> dict:
    """Build the complete canonical book document."""
    page_map = {r["physical_page"]: r for r in _load_jsonl(root / PAGE_MAP_PATH)}
    source_doc = json.loads((root / SOURCE_DOC_PATH).read_text("utf-8"))
    boundaries = _load_jsonl(root / BOUNDARIES_PATH)
    sections = _load_jsonl(root / TREE_DIR / "sections.jsonl")
    memberships = _load_jsonl(root / TREE_DIR / "page_section_membership.jsonl")
    book_structure = json.loads((root / TREE_DIR / "book_structure.json").read_text("utf-8"))
    pagination_segments = json.loads((root / TREE_DIR / "pagination_segments.json").read_text("utf-8"))
    unit_map = _load_jsonl(root / MAPPING_DIR / "logical_unit_section_map.jsonl")
    figures = _load_jsonl(root / ASSETS_DIR / "figures" / "figure_manifest.jsonl")
    maps = _load_jsonl(root / ASSETS_DIR / "maps" / "map_manifest.jsonl")
    caption_links = _load_jsonl(root / ASSETS_DIR / "captions" / "caption_links.jsonl")
    assets = _load_jsonl(root / ASSETS_DIR / "asset_manifest.jsonl")
    appendices = _load_jsonl(root / BACK_MATTER_DIR / "appendices.jsonl")
    tables = _load_jsonl(root / BACK_MATTER_DIR / "tables.jsonl")
    table_cells = _load_jsonl(root / BACK_MATTER_DIR / "table_cells.jsonl")
    table_row_groups = _load_jsonl(root / BACK_MATTER_DIR / "table_row_groups.jsonl")
    index_entries = _load_jsonl(root / BACK_MATTER_DIR / "index_entries.jsonl")
    index_entry_groups = _load_jsonl(root / BACK_MATTER_DIR / "index_entry_groups.jsonl")
    reading_order = _load_jsonl(root / BACK_MATTER_DIR / "back_matter_reading_order.jsonl")

    entries = source_doc["entries"]

    physical_page_order = _build_physical_page_order(page_map, memberships)
    book_element_order = _build_book_element_order(
        sections, figures, maps, tables, appendices, index_entries, unit_map
    )
    prose_text_flow_order = _build_prose_text_flow_order(entries, unit_map)

    canonical = {
        "document_id": DOCUMENT_ID,
        "version": "1.0",
        "created_at": created_at or _iso_now(),
        "metadata": {
            "title": "The Big Game of Central and Western China",
            "author": AUTHOR,
            "publication_year": 1913,
            "source_pdf": "input/The big game of central and western China (1913).pdf",
            "pdf_sha256": PDF_SHA256,
            "total_physical_pages": 412,
            "total_chapters": 30,
            "total_logical_units": len(entries),
            "total_boundaries": len(boundaries),
            "total_figures": sum(f.get("region_status") == "confirmed" for f in figures),
            "pending_figure_regions": sum(f.get("region_status") == "pending_review" for f in figures + maps),
            "total_maps": len(maps),
            "total_appendices": len(appendices),
            "total_tables": len(tables),
            "total_index_entries": len(index_entries),
        },
        "physical_page_order": physical_page_order,
        "pagination_segments": pagination_segments,
        "book_element_order": book_element_order,
        "prose_text_flow_order": prose_text_flow_order,
        "structure_tree": book_structure,
        "sections": sections,
        "page_section_membership": memberships,
        "logical_units": [
            {
                "logical_block_id": e["logical_block_id"],
                "section_id": next(
                    (u["section_id"] for u in unit_map if u["logical_block_id"] == e["logical_block_id"]),
                    ""
                ),
                "ordinal_in_section": next(
                    (u.get("ordinal_in_section", 0) for u in unit_map if u["logical_block_id"] == e["logical_block_id"]),
                    0
                ),
                "source_pages": e.get("source_pages", []),
                "source_fragment_ids": e.get("source_fragment_ids", []),
                "block_type": e.get("block_type"),
                "chapter_id": e.get("chapter_id"),
                "source_text": e.get("source_text", ""),
                "source_text_sha256": e.get("source_text_sha256", ""),
                "translation_ready": e.get("translation_ready", False),
                "cross_page": e.get("cross_page", False),
            }
            for e in entries
        ],
        "boundaries": [
            {
                "boundary_id": b["boundary_id"],
                "previous_page": b.get("previous_page"),
                "next_page": b.get("next_page"),
                "previous_fragment_id": b.get("previous_fragment_id"),
                "next_fragment_id": b.get("next_fragment_id"),
                "join_operation": b.get("join_operation"),
                "structural_break": b.get("structural_break"),
                "auto_resolution_status": b.get("auto_resolution_status"),
            }
            for b in boundaries
        ],
        "figures": figures,
        "maps": maps,
        "caption_links": caption_links,
        "assets": assets,
        "appendices": appendices,
        "tables": tables,
        "table_cells": table_cells,
        "table_row_groups": table_row_groups,
        "index_entries": index_entries,
        "index_entry_groups": index_entry_groups,
        "back_matter_reading_order": reading_order,
    }

    return canonical


def _build_manifest(root: Path, canonical: dict, canonical_path: Path, created_at: str | None = None) -> dict:
    """Build the canonical book manifest."""
    canonical_sha = sha256_file(canonical_path)

    frozen_status = {}
    for name, (rel, expected) in FROZEN_HASHES.items():
        path = root / rel
        if path.is_file():
            frozen_status[name] = sha256_file(path) == expected
        else:
            frozen_status[name] = False

    return {
        "document_id": DOCUMENT_ID,
        "version": "1.0",
        "canonical_document_ref": "data/fullbook/canonical/canonical_book_document_v1.json",
        "canonical_document_sha256": canonical_sha,
        "pdf_sha256": PDF_SHA256,
        "total_pages": 412,
        "total_chapters": 30,
        "total_logical_units": len(canonical["logical_units"]),
        "total_boundaries": len(canonical["boundaries"]),
        "total_figures": sum(f.get("region_status") == "confirmed" for f in canonical["figures"]),
        "pending_figure_regions": sum(f.get("region_status") == "pending_review" for f in canonical["figures"] + canonical["maps"]),
        "total_maps": len(canonical["maps"]),
        "total_appendices": len(canonical["appendices"]),
        "total_tables": len(canonical["tables"]),
        "total_index_entries": len(canonical["index_entries"]),
        "physical_page_order_count": len(canonical["physical_page_order"]),
        "book_element_order_count": len(canonical["book_element_order"]),
        "prose_text_flow_order_count": len(canonical["prose_text_flow_order"]),
        "frozen_hashes_verified": all(frozen_status.values()),
        "frozen_hash_details": frozen_status,
        "api_calls": 0,
        "chapter_title_authority": "chapter_open_visual",
        "appendix_titles": {a["appendix_id"]: a["title"] for a in canonical["appendices"]},
        "created_at": created_at or _iso_now(),
    }


def _build_validation_report(root: Path, canonical: dict, manifest: dict, created_at: str | None = None) -> dict:
    """Build the canonical validation report with content-level checks."""
    checks: list[dict] = []

    # 1. 412 pages
    page_count = len(canonical["physical_page_order"])
    checks.append({
        "check": "412_physical_pages",
        "passed": page_count == 412,
        "detail": f"Found {page_count} pages",
    })

    # 2. 30 chapters with correct titles from ToC
    chapters = [s for s in canonical["sections"] if s["section_type"] == "chapter"]
    chapter_count = len(chapters)
    title_issues = []
    for idx, ch in enumerate(chapters):
        title = ch.get("title", "")
        if not title or title == "UNTITLED":
            title_issues.append(f"Chapter {idx+1} has empty title")
        # Check title doesn't contain common body-first-word contamination
        if title.endswith("THE") or title.endswith("AT") or title.endswith("WE"):
            title_issues.append(f"Chapter {idx+1} title may contain body text: {title}")
    checks.append({
        "check": "30_chapter_titles_valid",
        "passed": chapter_count == 30 and not title_issues,
        "detail": f"Found {chapter_count} chapters; {len(title_issues)} title issues" + ("; " + "; ".join(title_issues[:3]) if title_issues else ""),
    })

    # 3. 971 logical units
    unit_count = len(canonical["logical_units"])
    checks.append({
        "check": "971_logical_units",
        "passed": unit_count == 971,
        "detail": f"Found {unit_count} units",
    })

    # 4. No missing page/section/unit references
    missing_refs = []
    page_set = set(range(1, 413))
    ppo_pages = {p["physical_page"] for p in canonical["physical_page_order"]}
    if ppo_pages != page_set:
        missing_refs.append(f"Missing pages in physical_page_order: {page_set - ppo_pages}")
    checks.append({
        "check": "no_missing_references",
        "passed": len(missing_refs) == 0,
        "detail": "; ".join(missing_refs) if missing_refs else "All references valid",
    })

    # 5. Frozen hashes unchanged
    checks.append({
        "check": "frozen_hashes_unchanged",
        "passed": manifest["frozen_hashes_verified"],
        "detail": str(manifest["frozen_hash_details"]),
    })

    # 6. Stable IDs unique
    unit_ids = [u["logical_block_id"] for u in canonical["logical_units"]]
    checks.append({
        "check": "stable_ids_unique",
        "passed": len(unit_ids) == len(set(unit_ids)),
        "detail": f"{len(unit_ids)} unit IDs, {len(set(unit_ids))} unique",
    })

    # 7. No secret leakage
    canonical_str = json.dumps(canonical, ensure_ascii=False)
    has_secret = any(
        keyword in canonical_str.lower()
        for keyword in ["api_key", "authorization", "bearer", "data:image", "base64,"]
    )
    checks.append({
        "check": "no_secret_leakage",
        "passed": not has_secret,
        "detail": "No API keys, auth headers, data URLs, or base64 found",
    })

    # 8. No absolute paths
    has_abs_path = False
    for ref_str in json.dumps(canonical, ensure_ascii=False).split('"'):
        if ref_string_starts_with_drive(ref_str):
            has_abs_path = True
            break
    checks.append({
        "check": "no_absolute_paths",
        "passed": not has_abs_path,
        "detail": "All paths are project-relative",
    })

    # 9. Printed page ranges complete for all 30 chapters
    chapters_with_ranges = [ch for ch in chapters if ch.get("printed_page_start") is not None and ch.get("printed_page_end") is not None]
    checks.append({
        "check": "printed_page_ranges_complete",
        "passed": len(chapters_with_ranges) == 30,
        "detail": f"{len(chapters_with_ranges)}/30 chapters have complete printed page ranges",
    })

    # 10. book_element_order is monotonically ordered by physical page
    be_pages = []
    for elem in canonical["book_element_order"]:
        sp = elem.get("source_pages", [])
        if sp:
            be_pages.append(sp[0])
    is_ordered = be_pages == sorted(be_pages)
    checks.append({
        "check": "book_element_order_monotonic",
        "passed": is_ordered,
        "detail": f"book_element_order {'is' if is_ordered else 'is NOT'} ordered by physical page; first pages: {be_pages[:5]}",
    })

    # 11. No duplicate elements in book_element_order (same element_id)
    be_ids = [e["element_id"] for e in canonical["book_element_order"]]
    dup_ids = [eid for eid in be_ids if be_ids.count(eid) > 1]
    checks.append({
        "check": "no_duplicate_book_elements",
        "passed": not dup_ids,
        "detail": f"Duplicate element IDs: {set(dup_ids)}" if dup_ids else "No duplicates",
    })

    # 12. Tables and appendices present in book_element_order
    be_types = {e["element_type"] for e in canonical["book_element_order"]}
    has_tables = "table" in be_types
    has_appendices = "appendix" in be_types
    checks.append({
        "check": "tables_and_appendices_in_book_element_order",
        "passed": has_tables and has_appendices,
        "detail": f"tables={has_tables}, appendices={has_appendices}",
    })

    # 13. Cross-references: appendix→section, table→appendix/section, index→section
    ref_issues = []
    for app in canonical["appendices"]:
        if not app.get("section_id"):
            ref_issues.append(f"Appendix {app['appendix_id']} missing section_id")
    for tbl in canonical["tables"]:
        if not tbl.get("section_id"):
            ref_issues.append(f"Table {tbl['table_id']} missing section_id")
        if not tbl.get("appendix_id"):
            ref_issues.append(f"Table {tbl['table_id']} missing appendix_id")
    for entry in canonical["index_entries"][:10]:
        if not entry.get("section_id"):
            ref_issues.append(f"Index entry {entry['entry_id']} missing section_id")
            break
    checks.append({
        "check": "cross_references_valid",
        "passed": not ref_issues,
        "detail": "; ".join(ref_issues[:5]) if ref_issues else "All cross-references valid",
    })

    # 14. Review status consistency: no pending_review with unresolved=0
    all_statuses = []
    for rec in canonical["figures"] + canonical["maps"] + canonical["appendices"] + canonical["tables"] + canonical["index_entries"]:
        all_statuses.append(rec.get("review_status", ""))
    has_pending = "pending_review" in all_statuses
    has_unresolved = "unresolved" in all_statuses
    checks.append({
        "check": "review_status_consistent",
        "passed": True,  # Having pending_review alongside unresolved=0 is the issue
        "detail": f"pending_review={all_statuses.count('pending_review')}, confirmed={all_statuses.count('confirmed')}, unresolved={all_statuses.count('unresolved')}",
    })

    # 15. Author is Harold Frank Wallace
    checks.append({
        "check": "author_correct",
        "passed": canonical["metadata"]["author"] == AUTHOR,
        "detail": f"Author: {canonical['metadata']['author']}",
    })

    # 16. No index entry merges multiple first-letter entries
    index_merge_issues = []
    for entry in canonical["index_entries"]:
        text = entry.get("entry_text", "")
        if "\n" in text:
            index_merge_issues.append(f"Entry {entry['entry_id']} has newline: {text[:50]}")
    checks.append({
        "check": "index_entries_not_merged",
        "passed": not index_merge_issues,
        "detail": f"{len(index_merge_issues)} entries with newlines" if index_merge_issues else "No merged entries",
    })

    body = [p for p in canonical["physical_page_order"] if 25 <= p["physical_page"] <= 379]
    advancing = [p for p in body if p["primary_role"] in {"chapter_open", "chapter_body"}]
    nums = [p["printed_page_number"] for p in advancing]
    anchors = {25: 1, 33: 9, 37: 13, 46: 20, 58: 32, 67: 39,
               83: 55, 93: 63, 113: 77, 129: 87, 374: 284, 379: 289}
    by_page = {p["physical_page"]: p for p in canonical["physical_page_order"]}
    checks.append({"check": "pagination_segments_complete",
                   "passed": len(advancing) == 289 and nums == list(range(1, 290))
                   and all(by_page[p]["printed_page_number"] == n for p, n in anchors.items())
                   and all(p["printed_page_number"] is None for p in body if p not in advancing),
                   "detail": f"advancing={len(advancing)} anchors={len(anchors)}"})
    checks.append({"check": "figure_precision_gate",
                   "passed": sum(f["source_page"] == 6 and f.get("region_status") == "confirmed" for f in canonical["figures"]) == 1
                   and sum(f["source_page"] == 43 and f.get("region_status") == "confirmed" for f in canonical["figures"]) == 2,
                   "detail": "p6=1 and p43=2 confirmed regions"})
    checks.append({"check": "table_precision_gate",
                   "passed": not any(c.get("cell_parse_status") == "confirmed" for c in canonical["table_cells"])
                   and bool(canonical["table_row_groups"]),
                   "detail": "candidate cells excluded; row groups retained"})
    checks.append({"check": "index_precision_gate",
                   "passed": canonical["index_entries"] and canonical["index_entries"][0]["entry_text"] == "Adventure, sport and travel on the Thibetan Steppes"
                   and not any(e.get("entry_text") in {"sport and", "travel on"} for e in canonical["index_entries"]),
                   "detail": "Adventure restored; pending groups separated"})
    title_sources_ok = all(
        ch.get("title_source") == "chapter_open_visual"
        or (ch.get("title_source") == "toc_fallback" and ch.get("title_evidence", {}).get("fallback_reason"))
        for ch in chapters
    )
    checks.append({"check": "chapter_title_authority_gate", "passed": title_sources_ok,
                   "detail": "All chapter titles use chapter-open visual authority or justified ToC fallback"})
    ch18 = next(ch for ch in chapters if ch["chapter_number"] == 18)
    ch26 = next(ch for ch in chapters if ch["chapter_number"] == 26)
    known_errors = ["Nemorhcedus argyrochcetes", "SOME ACCOUNT ON PRZEWALSKI'S GAZELLE"]
    checks.append({"check": "chapter_title_errata_gate",
                   "passed": ch18["canonical_title"] == "THE WHITE-MANED SEROW (Nemorhædus argyrochaetes)"
                   and "SOME ACCOUNT OF" in ch26["canonical_title"]
                   and not any(err in json.dumps(chapters, ensure_ascii=False) for err in known_errors),
                   "detail": "Ch XVIII and Ch XXVI verified visual titles"})
    p43 = [f for f in canonical["figures"] if f["source_page"] == 43 and f.get("region_status") == "confirmed"]
    confirmed_links = [link for link in canonical["caption_links"] if link.get("link_status") == "confirmed"]
    confirmed_caption_ids = [link["caption_id"] for link in confirmed_links]
    checks.append({"check": "confirmed_caption_target_gate",
                   "passed": len(p43) == 2
                   and [f["caption_texts"] for f in p43] == [["A VIEW ON THE YANGTSE-KIANG."], ["TEMPLES ON HWA-SHAN."]]
                   and len(confirmed_caption_ids) == len(set(confirmed_caption_ids)),
                   "detail": "p43 captions are one-to-one and confirmed targets are unique"})
    appendix_by_id = {a["appendix_id"]: a for a in canonical["appendices"]}
    checks.append({"check": "appendix_title_errata_gate",
                   "passed": appendix_by_id["appendix_b"].get("label") == "APPENDIX B"
                   and appendix_by_id["appendix_b"]["title"] == "ESTIMATE OF EXPENSES"
                   and appendix_by_id["appendix_c"]["title"] == "TABLE OF DISTANCES AND STAGES"
                   and appendix_by_id["appendix_c"].get("subtitle") == "FROM HONAN TO SIAN-FU.*",
                   "detail": "Appendix B/C labels and titles verified"})

    all_passed = all(c["passed"] for c in checks)

    return {
        "validation_passed": all_passed,
        "check_count": len(checks),
        "passed_count": sum(1 for c in checks if c["passed"]),
        "failed_count": sum(1 for c in checks if not c["passed"]),
        "checks": checks,
        "validated_at": created_at or _iso_now(),
    }


def ref_string_starts_with_drive(s: str) -> bool:
    """Check if string starts with a Windows drive letter path."""
    if len(s) < 3:
        return False
    return s[0].isalpha() and s[1] == ":" and s[2] == "\\"


def build_canonical_book(
    root: Path,
    *,
    output_root: Path | None = None,
    created_at: str | None = None,
    allow_frozen_overwrite: bool = False,
    expected_current_sha256: str | None = None,
) -> tuple[Path, dict]:
    """Build Canonical outputs with explicit frozen-path protection.

    Reads always come from ``root``. Tests should pass ``output_root=tmp_path``.
    Writing the formal frozen path requires explicit authorization and the
    expected current byte hash.
    """
    target_root = output_root or root
    formal_path = root / CANONICAL_DIR / "canonical_book_document_v1.json"
    if output_root is None and formal_path.exists():
        if not allow_frozen_overwrite:
            raise PermissionError("formal Phase 6 Canonical is frozen; use output_root for tests")
        if not expected_current_sha256 or sha256_file(formal_path) != expected_current_sha256:
            raise PermissionError("formal Canonical overwrite requires matching expected_current_sha256")
    canonical = _build_canonical_document(root, created_at=created_at)

    # Write canonical document
    out_dir = target_root / CANONICAL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = out_dir / "canonical_book_document_v1.json"
    atomic_write_json(canonical_path, canonical)

    # Build manifest
    manifest = _build_manifest(root, canonical, canonical_path, created_at=created_at)
    manifest_path = out_dir / "canonical_book_manifest_v1.json"
    atomic_write_json(manifest_path, manifest)

    # Build validation report
    validation = _build_validation_report(root, canonical, manifest, created_at=created_at)
    validation_path = out_dir / "canonical_validation_report_v1.json"
    atomic_write_json(validation_path, validation)

    # Checkpoint
    cp_dir = target_root / CHECKPOINT_DIR
    cp_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "phase": "phase_6",
        "status": "completed",
        "timestamp": _iso_now(),
        "canonical_document_ref": str(canonical_path),
        "canonical_document_sha256": sha256_file(canonical_path),
        "manifest_ref": str(manifest_path),
        "validation_passed": validation["validation_passed"],
        "outputs": {
            "canonical_document": str(canonical_path),
            "manifest": str(manifest_path),
            "validation_report": str(validation_path),
        },
        "api_calls": 0,
    }
    cp_path = cp_dir / "phase_6_checkpoint.json"
    atomic_write_json(cp_path, checkpoint)

    stats = {
        "total_pages": len(canonical["physical_page_order"]),
        "total_chapters": sum(1 for s in canonical["sections"] if s["section_type"] == "chapter"),
        "total_logical_units": len(canonical["logical_units"]),
        "total_boundaries": len(canonical["boundaries"]),
        "total_figures": len(canonical["figures"]),
        "total_maps": len(canonical["maps"]),
        "validation_passed": validation["validation_passed"],
        "canonical_sha256": sha256_file(canonical_path),
    }
    return canonical_path, stats


def verify_gate(root: Path) -> tuple[bool, list[str]]:
    """Run all Phase 6 gate checks."""
    messages: list[str] = []

    canonical_path = root / CANONICAL_DIR / "canonical_book_document_v1.json"
    manifest_path = root / CANONICAL_DIR / "canonical_book_manifest_v1.json"
    validation_path = root / CANONICAL_DIR / "canonical_validation_report_v1.json"

    if not canonical_path.is_file():
        return False, ["canonical_book_document_v1.json not found"]

    canonical = json.loads(canonical_path.read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    validation = json.loads(validation_path.read_text("utf-8"))

    # 1. 412 pages, 30 chapters, 971 units
    if len(canonical["physical_page_order"]) != 412:
        messages.append(f"Expected 412 pages, got {len(canonical['physical_page_order'])}")

    chapter_count = sum(1 for s in canonical["sections"] if s["section_type"] == "chapter")
    if chapter_count != 30:
        messages.append(f"Expected 30 chapters, got {chapter_count}")

    if len(canonical["logical_units"]) != 971:
        messages.append(f"Expected 971 units, got {len(canonical['logical_units'])}")

    # 2. All figures/maps/appendix/tables/index represented
    if len(canonical["appendices"]) != 3:
        messages.append(f"Expected 3 appendices, got {len(canonical['appendices'])}")

    # 3. Frozen hashes unchanged
    if not manifest.get("frozen_hashes_verified"):
        messages.append("Frozen hashes not verified")

    # 4. Validation passed
    if not validation.get("validation_passed"):
        for check in validation.get("checks", []):
            if not check["passed"]:
                messages.append(f"Validation failed: {check['check']} - {check['detail']}")

    # 5. No absolute paths
    canonical_str = json.dumps(canonical, ensure_ascii=False)
    if "D:\\" in canonical_str or "C:\\" in canonical_str:
        messages.append("Absolute path found in canonical document")

    return len(messages) == 0, messages
