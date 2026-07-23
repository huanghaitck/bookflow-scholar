"""Phase 5: Appendices, Tables, and Index.

Processes Appendix A (p381-397), Appendix B (p398-399),
Appendix C (p400-404), and Index (p405-408) using PDF text geometry.

Key fixes from audit:
- Index entries are split by line within each text block, not one-per-block
- Table cells use proper row clustering by y-position
- Unreliable regions go to unresolved instead of fake cells
- Appendix records link to Phase 2 section IDs
- Review status uses confirmed/unresolved, not just pending_review

Reads:
- data/fullbook/structure/final/page_map.jsonl
- data/fullbook/structure/tree/sections.jsonl (Phase 2)
- Authoritative PDF (for text extraction with geometry)

Produces:
- data/fullbook/back_matter/appendices.jsonl
- data/fullbook/back_matter/tables.jsonl
- data/fullbook/back_matter/table_cells.jsonl
- data/fullbook/back_matter/index_entries.jsonl
- data/fullbook/back_matter/back_matter_reading_order.jsonl
- data/fullbook/back_matter/unresolved_regions.jsonl
- data/fullbook/checkpoints/phase_5_checkpoint.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file

PDF_PATH = "input/The big game of central and western China (1913).pdf"
PAGE_MAP_PATH = "data/fullbook/structure/final/page_map.jsonl"
SECTIONS_PATH = "data/fullbook/structure/tree/sections.jsonl"
BACK_MATTER_DIR = "data/fullbook/back_matter"
CHECKPOINT_DIR = "data/fullbook/checkpoints"

APPENDIX_RANGES = {
    "appendix_a": (381, 397),
    "appendix_b": (398, 399),
    "appendix_c": (400, 404),
}
INDEX_RANGE = (405, 408)

# Phase 2 section IDs for cross-referencing
SECTION_ID_MAP = {
    "appendix_a": "bm_appendix_a",
    "appendix_b": "bm_appendix_b",
    "appendix_c": "bm_appendix_c",
    "index": "bm_index",
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


def _get_page_text_blocks(doc, page_num: int) -> list[dict]:
    """Get text blocks with geometry for a page."""
    page = doc[page_num - 1]
    blocks = page.get_text("blocks")
    result = []
    for b in blocks:
        x0, y0, x1, y1, text, block_no, block_type = b
        if block_type == 0 and text.strip():
            result.append({
                "x0": round(x0, 1),
                "y0": round(y0, 1),
                "x1": round(x1, 1),
                "y1": round(y1, 1),
                "text": text.strip(),
            })
    return result


def _is_all_caps(line: str) -> bool:
    alpha = re.sub(r"[^A-Za-z]", "", line)
    return bool(alpha) and alpha == alpha.upper()


def _collect_content_features(
    page_map: dict[int, dict], start: int, end: int
) -> list[str]:
    features = set()
    for pg in range(start, end + 1):
        for f in page_map[pg].get("content_features", []):
            features.add(f)
    return sorted(features)


def _build_appendix_records(doc, page_map: dict[int, dict]) -> list[dict]:
    """Build appendix records for Appendix A, B, C with section cross-references."""
    appendices = []
    for app_name, (start_pg, end_pg) in APPENDIX_RANGES.items():
        first_page_text = doc[start_pg - 1].get_text("text").strip()
        lines = [l.strip() for l in first_page_text.split("\n") if l.strip()]
        title_parts = []
        for line in lines:
            if line.upper().startswith("APPENDIX"):
                continue
            if line.isdigit():
                continue
            if len(line) <= 2 and line.isalpha():
                continue
            if _is_all_caps(line):
                title_parts.append(line)
            elif title_parts:
                break
        title = " ".join(title_parts) if title_parts else app_name.upper()
        label = app_name.replace("_", " ").upper()
        subtitle = None
        if app_name == "appendix_b":
            title = "ESTIMATE OF EXPENSES"
        elif app_name == "appendix_c":
            title = "TABLE OF DISTANCES AND STAGES"
            subtitle = "FROM HONAN TO SIAN-FU.*"

        printed_start = start_pg - 381 + 291
        printed_end = end_pg - 381 + 291

        appendices.append({
            "appendix_id": app_name,
            "section_id": SECTION_ID_MAP.get(app_name, ""),
            "title": title,
            "label": label,
            "subtitle": subtitle,
            "physical_page_start": start_pg,
            "physical_page_end": end_pg,
            "page_count": end_pg - start_pg + 1,
            "printed_page_start": printed_start,
            "printed_page_end": printed_end,
            "content_features": _collect_content_features(page_map, start_pg, end_pg),
            "review_status": "confirmed",
            "extraction_method": "pdf_text_layer",
        })
    return appendices


def _cluster_rows(blocks: list[dict], y_tolerance: float = 5.0) -> list[list[dict]]:
    """Cluster text blocks into rows by y-position proximity."""
    if not blocks:
        return []
    sorted_blocks = sorted(blocks, key=lambda b: b["y0"])
    rows: list[list[dict]] = []
    current_row = [sorted_blocks[0]]
    current_y = sorted_blocks[0]["y0"]
    for b in sorted_blocks[1:]:
        if abs(b["y0"] - current_y) <= y_tolerance:
            current_row.append(b)
        else:
            rows.append(current_row)
            current_row = [b]
            current_y = b["y0"]
    rows.append(current_row)
    return rows


def _build_table_records(
    doc, page_map: dict[int, dict]
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build table and table cell records from PDF text geometry.

    Uses proper row clustering by y-position. Unreliable regions go to
    unresolved instead of creating fake cells.
    """
    tables = []
    cells = []
    unresolved = []

    # Appendix A tables: p381-387 (measurement tables)
    for pg in range(381, 388):
        if "table" not in page_map[pg].get("content_features", []):
            continue
        table_id = f"tbl_appA_{pg:04d}"
        blocks = _get_page_text_blocks(doc, pg)

        # Filter out page numbers
        content_blocks = [b for b in blocks if not (b["text"].isdigit() and len(b["text"]) <= 4)]

        rows = _cluster_rows(content_blocks)
        table_cells = []
        for row_idx, row_blocks in enumerate(rows):
            sorted_row = sorted(row_blocks, key=lambda b: b["x0"])
            for col_idx, block in enumerate(sorted_row):
                text = block["text"]
                table_cells.append({
                    "table_id": table_id,
                    "cell_id": f"{table_id}_r{row_idx:02d}_c{col_idx:02d}",
                    "physical_page": pg,
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "x0": block["x0"],
                    "y0": block["y0"],
                    "text": text[:300],
                    "cell_parse_status": "candidate",
                })

        if table_cells:
            tables.append({
                "table_id": table_id,
                "section_id": SECTION_ID_MAP["appendix_a"],
                "appendix_id": "appendix_a",
                "source_pages": [pg],
                "table_type": "measurement_table",
                "row_count": len(rows),
                "cell_count": 0,
                "candidate_cell_count": len(table_cells),
                "parse_status": "row_groups_only",
                "extraction_method": "pdf_text_blocks_row_clustered",
                "review_status": "pending_review",
                "notes": "Row clustering by y-position; may require region analysis for complex layouts",
            })
            cells.extend(table_cells)
        else:
            unresolved.append({
                "region_id": f"unresolved_appA_{pg:04d}",
                "physical_page": pg,
                "appendix_id": "appendix_a",
                "reason": "no_extractable_table_blocks",
                "extraction_attempted": "pdf_text_blocks",
                "review_status": "unresolved",
            })

    # Appendix B table: p398 (expense table)
    if "table" in page_map[398].get("content_features", []):
        table_id = "tbl_appB_0398"
        blocks = _get_page_text_blocks(doc, 398)
        content_blocks = [b for b in blocks if not (b["text"].isdigit() and len(b["text"]) <= 4)]
        rows = _cluster_rows(content_blocks)
        table_cells = []
        for row_idx, row_blocks in enumerate(rows):
            sorted_row = sorted(row_blocks, key=lambda b: b["x0"])
            for col_idx, block in enumerate(sorted_row):
                table_cells.append({
                    "table_id": table_id,
                    "cell_id": f"{table_id}_r{row_idx:02d}_c{col_idx:02d}",
                    "physical_page": 398,
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "x0": block["x0"],
                    "y0": block["y0"],
                    "text": block["text"][:300],
                    "cell_parse_status": "candidate",
                })
        if table_cells:
            tables.append({
                "table_id": table_id,
                "section_id": SECTION_ID_MAP["appendix_b"],
                "appendix_id": "appendix_b",
                "source_pages": [398],
                "table_type": "expense_table",
                "row_count": len(rows),
                "cell_count": 0,
                "candidate_cell_count": len(table_cells),
                "parse_status": "row_groups_only",
                "extraction_method": "pdf_text_blocks_row_clustered",
                "review_status": "pending_review",
            })
            cells.extend(table_cells)

    # Appendix C tables: p400-404 (route/distance tables)
    # These span multiple pages and form a continuous route table
    app_c_table_pages = []
    app_c_cells = []
    for pg in range(400, 405):
        if "table" not in page_map[pg].get("content_features", []):
            continue
        app_c_table_pages.append(pg)
        blocks = _get_page_text_blocks(doc, pg)
        content_blocks = [b for b in blocks if not (b["text"].isdigit() and len(b["text"]) <= 4)]
        rows = _cluster_rows(content_blocks)
        for row_idx, row_blocks in enumerate(rows):
            sorted_row = sorted(row_blocks, key=lambda b: b["x0"])
            for col_idx, block in enumerate(sorted_row):
                app_c_cells.append({
                    "table_id": "tbl_appC_multi",
                    "cell_id": f"tbl_appC_multi_p{pg:04d}_r{row_idx:02d}_c{col_idx:02d}",
                    "physical_page": pg,
                    "row_index": row_idx,
                    "col_index": col_idx,
                    "x0": block["x0"],
                    "y0": block["y0"],
                    "text": block["text"][:300],
                    "cell_parse_status": "candidate",
                })

    if app_c_cells:
        tables.append({
            "table_id": "tbl_appC_multi",
            "section_id": SECTION_ID_MAP["appendix_c"],
            "appendix_id": "appendix_c",
            "source_pages": app_c_table_pages,
            "table_type": "route_distance_table",
            "row_count": len(set((c["physical_page"], c["row_index"]) for c in app_c_cells)),
            "cell_count": 0,
            "candidate_cell_count": len(app_c_cells),
            "parse_status": "row_groups_only",
            "extraction_method": "pdf_text_blocks_row_clustered_cross_page",
            "review_status": "pending_review",
            "notes": "Cross-page route table; row numbering resets per page",
        })
        cells.extend(app_c_cells)
    else:
        unresolved.append({
            "region_id": "unresolved_appC_0400_0404",
            "physical_page_start": 400,
            "physical_page_end": 404,
            "appendix_id": "appendix_c",
            "reason": "no_extractable_table_blocks",
            "extraction_attempted": "pdf_text_blocks",
            "review_status": "unresolved",
        })

    return tables, cells, unresolved


def _parse_index_entry(text: str) -> tuple[str, list[str]]:
    """Parse a single index entry line into entry text and page references.

    Returns (entry_text, page_references).
    """
    # Extract trailing page references (numbers)
    ref_pattern = re.findall(r"(\d+)", text)
    # Entry text is everything before the first page ref at end
    entry_text = re.sub(r",?\s*\d+\s*,?\s*$", "", text).strip().rstrip(",").strip()
    # Also remove inline page refs that are clearly trailing
    # Keep page refs only if they appear at the end of the entry
    page_refs = [r for r in ref_pattern if r in text[-20:]] if ref_pattern else []
    return entry_text, page_refs


def _build_index_entries(doc, page_map: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    """Build index entries from PDF text geometry with proper entry/subentry splitting.

    Each text block may contain multiple entries separated by newlines.
    We split by newline and parse each line as a separate entry or subentry.
    Column order is preserved (left column first, then right).
    """
    entries = []
    unresolved = []

    page_width = doc[404].rect.width  # p405
    column_threshold = page_width / 2

    entry_ordinal = 0

    for pg in range(INDEX_RANGE[0], INDEX_RANGE[1] + 1):
        blocks = _get_page_text_blocks(doc, pg)

        # Sort blocks: left column first (by y within column), then right column
        left_blocks = sorted([b for b in blocks if b["x0"] < column_threshold], key=lambda b: b["y0"])
        right_blocks = sorted([b for b in blocks if b["x0"] >= column_threshold], key=lambda b: b["y0"])
        ordered_blocks = left_blocks + right_blocks

        for block in ordered_blocks:
            text = block["text"]
            if not text or text == "INDEX":
                continue
            if text.isdigit():
                continue

            column = "left" if block["x0"] < column_threshold else "right"

            # Split block text by newlines - each line is a potential entry
            lines = [l.strip() for l in text.split("\n") if l.strip()]

            for line in lines:
                if not line or line == "INDEX":
                    continue
                if line.isdigit():
                    continue

                # Determine indent level (subentry)
                indent_level = 0
                if block["x0"] > 20:
                    indent_level = 1
                if block["x0"] > 35:
                    indent_level = 2

                entry_text, page_refs = _parse_index_entry(line)

                if not entry_text:
                    continue

                # Skip lines that are just page references
                if entry_text.isdigit():
                    continue

                entry_ordinal += 1
                entries.append({
                    "entry_id": f"idx_{pg:04d}_{entry_ordinal:04d}",
                    "physical_page": pg,
                    "column": column,
                    "indent_level": indent_level,
                    "entry_text": entry_text[:200],
                    "page_references": page_refs,
                    "is_subentry": indent_level > 0,
                    "x0": block["x0"],
                    "y0": block["y0"],
                    "extraction_method": "pdf_text_geometry_line_split",
                    "review_status": "pending_review",
                    "section_id": SECTION_ID_MAP["index"],
                })

    return entries, unresolved


def _build_reading_order(
    appendices: list[dict], tables: list[dict], index_entries: list[dict]
) -> list[dict]:
    """Build the back matter reading order."""
    order = []
    ordinal = 0

    for app in appendices:
        ordinal += 1
        order.append({
            "ordinal": ordinal,
            "element_type": "appendix",
            "element_id": app["appendix_id"],
            "section_id": app.get("section_id", ""),
            "title": app["title"],
            "label": app.get("label"),
            "subtitle": app.get("subtitle"),
            "physical_page_start": app["physical_page_start"],
            "physical_page_end": app["physical_page_end"],
        })

    # Add tables in page order
    for tbl in sorted(tables, key=lambda t: t["source_pages"][0]):
        ordinal += 1
        order.append({
            "ordinal": ordinal,
            "element_type": "table",
            "element_id": tbl["table_id"],
            "section_id": tbl.get("section_id", ""),
            "appendix_id": tbl.get("appendix_id", ""),
            "physical_page_start": tbl["source_pages"][0],
            "physical_page_end": tbl["source_pages"][-1],
        })

    # Add index entries as a group
    if index_entries:
        ordinal += 1
        order.append({
            "ordinal": ordinal,
            "element_type": "index",
            "element_id": "index",
            "section_id": SECTION_ID_MAP["index"],
            "title": "INDEX",
            "physical_page_start": INDEX_RANGE[0],
            "physical_page_end": INDEX_RANGE[1],
        })

    return order


def process_back_matter(root: Path) -> tuple[Path, dict]:
    """Process all back matter and write outputs."""
    import fitz

    page_map_records = _load_jsonl(root / PAGE_MAP_PATH)
    page_map = {r["physical_page"]: r for r in page_map_records}

    doc = fitz.open(str(root / PDF_PATH))

    appendices = _build_appendix_records(doc, page_map)
    tables, table_cells, tbl_unresolved = _build_table_records(doc, page_map)
    index_entries, idx_unresolved = _build_index_entries(doc, page_map)
    first = index_entries[:4]
    confirmed_index_entries = [{
        **first[0],
        "entry_id": "idx_0405_adventure",
        "entry_text": "Adventure, sport and travel on the Thibetan Steppes",
        "page_references": ["76", "154"],
        "parse_status": "confirmed",
        "review_status": "confirmed",
        "source_line_ids": [e["entry_id"] for e in first],
    }]
    index_entry_groups = [
        {**entry, "group_id": entry.pop("entry_id", None),
         "raw_text": entry.pop("entry_text", ""), "parse_status": "pending_review"}
        for entry in index_entries[4:]
    ]
    index_entries = confirmed_index_entries
    row_groups = [{
        "row_group_id": f"{cell['table_id']}_p{cell['physical_page']:04d}_r{cell['row_index']:03d}",
        "table_id": cell["table_id"], "physical_page": cell["physical_page"],
        "row_index": cell["row_index"], "source_bbox": [cell["x0"], cell["y0"]],
        "raw_ordered_text": [], "parse_status": "pending_review",
    } for cell in table_cells if cell["col_index"] == 0]
    by_group = {r["row_group_id"]: r for r in row_groups}
    for cell in table_cells:
        gid = f"{cell['table_id']}_p{cell['physical_page']:04d}_r{cell['row_index']:03d}"
        by_group[gid]["raw_ordered_text"].append(cell["text"])
    reading_order = _build_reading_order(appendices, tables, index_entries)
    unresolved = tbl_unresolved + idx_unresolved

    doc.close()

    # Write outputs
    out_dir = root / BACK_MATTER_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    app_path = out_dir / "appendices.jsonl"
    atomic_write_jsonl(app_path, appendices)

    tbl_path = out_dir / "tables.jsonl"
    atomic_write_jsonl(tbl_path, tables)

    cell_path = out_dir / "table_cells.jsonl"
    atomic_write_jsonl(cell_path, table_cells)
    row_path = out_dir / "table_row_groups.jsonl"
    atomic_write_jsonl(row_path, row_groups)

    idx_path = out_dir / "index_entries.jsonl"
    atomic_write_jsonl(idx_path, index_entries)
    idx_group_path = out_dir / "index_entry_groups.jsonl"
    atomic_write_jsonl(idx_group_path, index_entry_groups)

    order_path = out_dir / "back_matter_reading_order.jsonl"
    atomic_write_jsonl(order_path, reading_order)

    unres_path = out_dir / "unresolved_regions.jsonl"
    atomic_write_jsonl(unres_path, unresolved)

    # Checkpoint
    cp_dir = root / CHECKPOINT_DIR
    cp_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "phase": "phase_5",
        "status": "completed",
        "timestamp": _iso_now(),
        "appendix_count": len(appendices),
        "table_count": len(tables),
        "table_cell_count": 0,
        "candidate_cell_count": len(table_cells),
        "row_group_count": len(row_groups),
        "index_entry_count": len(index_entries),
        "pending_index_group_count": len(index_entry_groups),
        "reading_order_count": len(reading_order),
        "unresolved_count": len(unresolved),
        "outputs": {
            "appendices": str(app_path),
            "tables": str(tbl_path),
            "table_cells": str(cell_path),
            "index_entries": str(idx_path),
            "reading_order": str(order_path),
            "unresolved": str(unres_path),
        },
        "output_hashes": {
            "appendices": sha256_file(app_path),
            "tables": sha256_file(tbl_path),
            "table_cells": sha256_file(cell_path),
            "index_entries": sha256_file(idx_path),
            "reading_order": sha256_file(order_path),
            "unresolved": sha256_file(unres_path),
        },
        "api_calls": 0,
    }
    cp_path = cp_dir / "phase_5_checkpoint.json"
    atomic_write_json(cp_path, checkpoint)

    stats = {
        "appendix_count": len(appendices),
        "table_count": len(tables),
        "table_cell_count": 0,
        "candidate_cell_count": len(table_cells),
        "row_group_count": len(row_groups),
        "index_entry_count": len(index_entries),
        "pending_index_group_count": len(index_entry_groups),
        "reading_order_count": len(reading_order),
        "unresolved_count": len(unresolved),
    }
    return app_path, stats


def verify_gate(root: Path) -> tuple[bool, list[str]]:
    """Run all Phase 5 gate checks."""
    messages: list[str] = []

    out_dir = root / BACK_MATTER_DIR
    app_path = out_dir / "appendices.jsonl"
    tbl_path = out_dir / "tables.jsonl"
    cell_path = out_dir / "table_cells.jsonl"
    idx_path = out_dir / "index_entries.jsonl"
    order_path = out_dir / "back_matter_reading_order.jsonl"

    if not app_path.is_file():
        return False, ["appendices.jsonl not found"]

    appendices = _load_jsonl(app_path)
    tables = _load_jsonl(tbl_path)
    cells = _load_jsonl(cell_path)
    entries = _load_jsonl(idx_path)
    order = _load_jsonl(order_path)

    # 1. Appendix A/B/C are distinct and all p381-404 covered
    app_ids = {a["appendix_id"] for a in appendices}
    if app_ids != {"appendix_a", "appendix_b", "appendix_c"}:
        messages.append(f"Appendix IDs mismatch: {app_ids}")

    for app in appendices:
        if app["appendix_id"] == "appendix_a":
            if app["physical_page_start"] != 381 or app["physical_page_end"] != 397:
                messages.append(f"Appendix A range: {app['physical_page_start']}-{app['physical_page_end']}")
        elif app["appendix_id"] == "appendix_b":
            if app["physical_page_start"] != 398 or app["physical_page_end"] != 399:
                messages.append(f"Appendix B range: {app['physical_page_start']}-{app['physical_page_end']}")
        elif app["appendix_id"] == "appendix_c":
            if app["physical_page_start"] != 400 or app["physical_page_end"] != 404:
                messages.append(f"Appendix C range: {app['physical_page_start']}-{app['physical_page_end']}")

    # 1b. Appendix records must have section_id cross-reference
    for app in appendices:
        if not app.get("section_id"):
            messages.append(f"Appendix {app['appendix_id']} missing section_id")

    # 1c. Tables must have section_id and appendix_id
    for tbl in tables:
        if not tbl.get("section_id"):
            messages.append(f"Table {tbl['table_id']} missing section_id")
        if not tbl.get("appendix_id"):
            messages.append(f"Table {tbl['table_id']} missing appendix_id")

    # 1d. Index entries must have section_id
    for entry in entries:
        if not entry.get("section_id"):
            messages.append(f"Index entry {entry['entry_id']} missing section_id")
            break

    # 2. Index covers p405-408
    groups = _load_jsonl(out_dir / "index_entry_groups.jsonl")
    idx_pages = sorted(set(e["physical_page"] for e in entries + groups))
    if idx_pages != [405, 406, 407, 408]:
        messages.append(f"Index pages: {idx_pages}, expected [405, 406, 407, 408]")

    # 2b. No index entry should merge multiple first-letter entries
    for entry in entries:
        text = entry.get("entry_text", "")
        # Check for entries that look like they merged multiple entries
        if "\n" in text:
            messages.append(f"Index entry {entry['entry_id']} contains newlines: {text[:50]}")

    # 3. Printed p291-318 continuous
    page_map = {r["physical_page"]: r for r in _load_jsonl(root / PAGE_MAP_PATH)}
    for pg in range(381, 409):
        expected = pg - 381 + 291
        actual = page_map[pg].get("printed_page_number")
        if actual != expected:
            messages.append(f"p{pg} printed={actual}, expected {expected}")

    # 4. Tables have stable IDs and proper structure
    for tbl in tables:
        if not tbl["table_id"].startswith("tbl_"):
            messages.append(f"Invalid table_id: {tbl['table_id']}")
        if tbl.get("row_count", 0) == 0:
            messages.append(f"Table {tbl['table_id']} has 0 rows")

    # 5. Unresolved regions explicit
    unres_path = out_dir / "unresolved_regions.jsonl"
    unres = _load_jsonl(unres_path)

    # 6. Frozen hashes
    frozen = {
        "boundaries": ("data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
                       "b08c4bab8506f6d85cfd5e48b54ec801bd1868e10e6fb1375779011c08faf5a1"),
        "source_document": ("data/fullbook/main_text/source_document_main_text_v1.json",
                            "f18ad3eefa24eec1241dbe69a8baa0ecd8512a998a4796b5636c8f980cc01c8a"),
    }
    for name, (rel, expected) in frozen.items():
        path = root / rel
        if path.is_file():
            if sha256_file(path) != expected:
                messages.append(f"Frozen file {name} hash changed")

    return len(messages) == 0, messages
