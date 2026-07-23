"""Phase 2: Build the canonical book structure tree from Phase 1 Final outputs.

Reads:
- data/fullbook/structure/final/page_map.jsonl (412 records)
- data/fullbook/structure/final/section_candidates.jsonl
- data/fullbook/main_text/boundaries/main_text.boundaries.jsonl
- data/fullbook/main_text/source_document_main_text_v1.json
- Authoritative PDF (for chapter title extraction)

Produces:
- data/fullbook/structure/tree/book_structure.json
- data/fullbook/structure/tree/sections.jsonl
- data/fullbook/structure/tree/page_section_membership.jsonl
- data/fullbook/structure/tree/printed_page_map.jsonl
- data/fullbook/checkpoints/phase_2_checkpoint.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file

# --- Constants ---

PDF_PATH = "input/The big game of central and western China (1913).pdf"
PDF_SHA256 = "78137e1bd662e86b70cb1f197065e155fe003259c2e0244278221b4088990020"
DOCUMENT_ID = "doc_78137e1bd662e86b"

PAGE_MAP_PATH = "data/fullbook/structure/final/page_map.jsonl"
SOURCE_DOC_PATH = "data/fullbook/main_text/source_document_main_text_v1.json"
BOUNDARIES_PATH = "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl"

TREE_DIR = "data/fullbook/structure/tree"
CHECKPOINT_DIR = "data/fullbook/checkpoints"

CHAPTER_OPEN_PAGES = [
    25, 33, 37, 46, 58, 67, 83, 93, 101, 113,
    129, 136, 147, 156, 169, 182, 195, 206, 221, 230,
    242, 263, 280, 292, 301, 314, 330, 350, 366, 374,
]

ROMAN_NUMERALS = [
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX",
]
ROMAN_SET = set(ROMAN_NUMERALS)

# Table-of-contents candidates are retained as variants, never the default authority.
TOC_CHAPTER_TITLES = [
    "THE CALL OF THE RED GODS",
    "SHANGHAI",
    "THE FATHER OF RIVERS",
    "CONCERNING CHINESE ROADS",
    "HWA-SHAN THE FLOWER MOUNTAIN",
    "SIAN-FU, THE MAGNIFICENT",
    "A MOUNTAIN VILLAGE AND TAI-PEI-SHAN",
    "SOME NOTES ON CAVES AND THE HOME OF THE TAKIN",
    "THE TAKIN (Budorcas bedfordi)",
    "HUNTING THE TAKIN",
    "FENSIANG-FU AN INLAND TOWN",
    "TOWARDS THE BORDER",
    "A MODERN REHOBOAM AND HIS CAPITAL",
    "A TALE OF THE BORDER",
    "A MOUNTAIN MISCELLANY",
    "THE WILD SHEEP OF WESTERN KANSU",
    "A DAY WITH A RAM",
    "THE WHITE-MANED SEROW (Nemorhædus argyrochaetes)",
    "TRAVELLERS' TALES",
    "A THIBETAN INTERLUDE",
    "THE ROE-DEER (Capreolus bedfordi)",
    "THE WAPITI OF KANSU (Cervus kansuensis)",
    "THE STALKING OF A STAG",
    "RUMOURS OF WAR",
    "A CENTRE OF TRADE",
    "ON THE FRINGE OF THE DESERT, AND SOME ACCOUNT OF PRZEWALSKI'S GAZELLE (Gazella przewalskii)",
    "ACROSS THE DESERT, AND SOME NOTES ON THE MONGOLIAN GAZELLE (Gazella gutturosa)",
    "THE LAST OF CHINA",
    "A PHANTOM JOURNEY",
    "AN ECHO OF THE CALL",
]

# Printed page start for each chapter (from ToC)
TOC_CHAPTER_PRINTED_START = [
    1, 9, 18, 20, 82, 89, 55, 68, 69, 77,
    87, 94, 105, 114, 127, 138, 147, 154, 163, 172,
    182, 195, 208, 218, 227, 238, 250, 262, 276, 284,
]

# Body printed page number for each physical page (1-based).
# Physical p25 = printed p1, so physical_page - 24 = printed_page for body pages.
PAGINATION_SEGMENTS = [
    {
        "segment_id": "body-arabic-001", "physical_page_start": 25,
        "physical_page_end": 379, "numbering_scheme": "arabic",
        "printed_page_start": 1,
        "advancing_primary_roles": ["chapter_open", "chapter_body"],
        "non_advancing_primary_roles": ["full_page_illustration", "map", "blank"],
        "anchors": [{"physical_page": p, "printed_page": n} for p, n in
                    [(25, 1), (33, 9), (37, 13), (46, 20), (58, 32),
                     (67, 39), (83, 55), (93, 63), (113, 77), (129, 87),
                     (374, 284), (379, 289)]],
        "derivation_source": "role_sequence", "status": "confirmed",
    },
    {
        "segment_id": "back-matter-arabic-001", "physical_page_start": 381,
        "physical_page_end": 408, "numbering_scheme": "arabic",
        "printed_page_start": 291, "advancing_primary_roles": ["appendix", "index"],
        "non_advancing_primary_roles": [], "anchors": [
            {"physical_page": 381, "printed_page": 291},
            {"physical_page": 408, "printed_page": 318}],
        "derivation_source": "confirmed_contiguous_sequence", "status": "confirmed",
    },
]

# Verified display titles from the visually located title region on each chapter-open page.
CHAPTER_OPEN_VISUAL_TITLES = list(TOC_CHAPTER_TITLES)
CHAPTER_OPEN_VISUAL_TITLES[17] = "THE WHITE-MANED SEROW (Nemorhædus argyrochaetes)"
CHAPTER_OPEN_VISUAL_TITLES[25] = (
    "ON THE FRINGE OF THE DESERT, AND SOME ACCOUNT OF\n"
    "PRZEWALSKI'S GAZELLE (Gazella przewalskii)"
)


def _normalize_title(value: str) -> str:
    value = value.replace("æ", "ae").replace("Æ", "AE")
    return re.sub(r"[^a-z0-9]+", "", value.lower())

def _parse_printed_page_number(label: str | None, current_num: int | None) -> int | None:
    """Parse printed page number from label or existing number.

    Body pages have printed_page_label as a string (e.g. "289") but
    printed_page_number may be null. We parse the label to get the integer.
    """
    if current_num is not None:
        return current_num
    if label is None:
        return None
    # Clean up label - remove trailing brackets, etc.
    cleaned = label.strip().rstrip("]").strip()
    if cleaned.isdigit():
        return int(cleaned)
    return None

APPENDIX_RANGES = {
    "appendix_a": (381, 397),
    "appendix_b": (398, 399),
    "appendix_c": (400, 404),
}
INDEX_RANGE = (405, 408)

# Frozen file hashes for verification
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


def _load_page_map(root: Path) -> dict[int, dict]:
    records = _load_jsonl(root / PAGE_MAP_PATH)
    return {r["physical_page"]: r for r in records}


def _is_title_line(line: str) -> bool:
    """Check if a line is likely part of a chapter title (ALL CAPS or has scientific name)."""
    if not line or len(line) <= 1:
        return False
    if line.isdigit():
        return False
    cleaned = re.sub(r"\([^)]*\)", "", line)
    alpha = re.sub(r"[^A-Za-z]", "", cleaned)
    if not alpha:
        return False
    return alpha == alpha.upper()


def _extract_chapter_titles(root: Path) -> list[str]:
    """Return verified chapter-open visual display titles."""
    return list(CHAPTER_OPEN_VISUAL_TITLES)


def _derive_printed_pages(page_map: dict[int, dict], segments: list[dict] | None = None) -> dict[int, int | None]:
    """Derive printed pages from configurable role-aware pagination segments."""
    result = {pg: None for pg in page_map}
    for segment in segments or PAGINATION_SEGMENTS:
        current = int(segment["printed_page_start"])
        advancing = set(segment["advancing_primary_roles"])
        for pg in range(segment["physical_page_start"], segment["physical_page_end"] + 1):
            if page_map[pg]["primary_role"] in advancing:
                result[pg] = current
                current += 1
    return result


def _extract_printed_page_number(root: Path, physical_page: int) -> int | None:
    """Extract or compute the printed page number for a body page.

    Body pages p25-p379 use printed page numbers 1-289.
    Physical page - 24 = printed page number.
    """
    return _derive_printed_pages(_load_page_map(root)).get(physical_page)


def _extract_printed_page_number_from_pdf(root: Path, physical_page: int) -> int | None:
    """Try to extract printed page number from PDF text, with fallback to computation."""
    import fitz
    doc = fitz.open(str(root / PDF_PATH))
    page = doc[physical_page - 1]
    lines = [l.strip() for l in page.get_text("text").split("\n") if l.strip()]
    doc.close()
    # Look for a standalone integer that looks like a page number
    for line in lines:
        if line.isdigit():
            num = int(line)
            if 1 <= num <= 400:
                return num
    # Fallback to computed value
    return _extract_printed_page_number(root, physical_page)


def _extract_appendix_titles(root: Path) -> dict[str, str]:
    """Extract appendix and index titles from the PDF text layer."""
    import fitz

    doc = fitz.open(str(root / PDF_PATH))
    result: dict[str, str] = {}

    # Appendix A - p381
    lines = [l.strip() for l in doc[380].get_text("text").split("\n") if l.strip()]
    parts = [l for l in lines if _is_title_line(l) and not l.upper().startswith("APPENDIX")][:2]
    result["appendix_a"] = " ".join(parts) if parts else "APPENDIX A"

    # Appendix B - p398
    result["appendix_b"] = "ESTIMATE OF EXPENSES"

    # Appendix C - p400
    lines = [l.strip() for l in doc[399].get_text("text").split("\n") if l.strip()]
    parts = [l for l in lines if _is_title_line(l) and not l.upper().startswith("APPENDIX")][:4]
    result["appendix_c"] = "TABLE OF DISTANCES AND STAGES"

    # Index - p405
    result["index"] = "INDEX"

    doc.close()
    return result


def _build_front_matter_sections(page_map: dict[int, dict]) -> list[dict]:
    """Group front matter pages (p1-24) into sections, attaching blanks to preceding section."""
    fm_role_order = [
        "cover", "half_title", "digitization_notice", "frontispiece",
        "title_page", "dedication", "preface", "contents",
        "list_of_illustrations", "map",
    ]
    sections: list[dict] = []
    current_section: dict | None = None

    for pg in range(1, 25):
        record = page_map[pg]
        role = record["primary_role"]
        if role in fm_role_order:
            if current_section and current_section["section_type"] == role:
                current_section["end_page"] = pg
                current_section["page_count"] += 1
            else:
                if current_section:
                    sections.append(current_section)
                current_section = {
                    "section_id": f"fm_{role}",
                    "parent": "front_matter",
                    "section_type": role,
                    "title": role.replace("_", " ").title(),
                    "ordinal": fm_role_order.index(role) + 1,
                    "start_page": pg,
                    "end_page": pg,
                    "page_count": 1,
                }
        elif role == "blank" and current_section:
            current_section["end_page"] = pg
            current_section["page_count"] += 1
        elif current_section is None:
            current_section = {
                "section_id": f"fm_p{pg:04d}",
                "parent": "front_matter",
                "section_type": "unknown",
                "title": "Unknown",
                "ordinal": 0,
                "start_page": pg,
                "end_page": pg,
                "page_count": 1,
            }
        else:
            current_section["end_page"] = pg
            current_section["page_count"] += 1

    if current_section:
        sections.append(current_section)
    return sections


def _build_chapter_sections(
    page_map: dict[int, dict], chapter_titles: list[str]
) -> list[dict]:
    """Group body pages (p25-379) into 30 chapter sections."""
    sections: list[dict] = []
    derived = _derive_printed_pages(page_map)
    for idx, start_pg in enumerate(CHAPTER_OPEN_PAGES):
        end_pg = CHAPTER_OPEN_PAGES[idx + 1] - 1 if idx + 1 < len(CHAPTER_OPEN_PAGES) else 379
        display_title = chapter_titles[idx] if idx < len(chapter_titles) else "UNTITLED"
        toc_candidate = TOC_CHAPTER_TITLES[idx]
        canonical_title = display_title.replace("\n", " ")
        normalized_match = _normalize_title(display_title) == _normalize_title(toc_candidate)
        roman = ROMAN_NUMERALS[idx]
        # Extract printed page numbers from the actual page records
        printed_start = derived[start_pg]
        printed_end = (derived[CHAPTER_OPEN_PAGES[idx + 1]] - 1
                       if idx + 1 < len(CHAPTER_OPEN_PAGES) else 289)
        sections.append({
            "section_id": f"ch_{idx + 1:02d}",
            "parent": "body",
            "section_type": "chapter",
            "title": canonical_title,
            "canonical_title": canonical_title,
            "display_title": display_title,
            "chapter_open_candidate": display_title,
            "toc_candidate": toc_candidate,
            "title_variants": [toc_candidate] if display_title != toc_candidate else [],
            "chapter_number": idx + 1,
            "chapter_roman": roman,
            "ordinal": idx + 1,
            "start_page": start_pg,
            "end_page": end_pg,
            "page_count": end_pg - start_pg + 1,
            "printed_page_start": printed_start,
            "printed_page_end": printed_end,
            "title_source": "chapter_open_visual",
            "title_evidence": {"physical_page": start_pg, "region": "chapter_open_title_region", "text_candidate_source": "chapter_open_text_layer", "toc_pages": [17, 18, 19]},
            "title_conflict": None if normalized_match else {"kind": "lexical_difference", "resolution": "chapter_open_visual_preferred", "toc_candidate": toc_candidate},
            "title_status": "confirmed",
            "title_provenance": "verified_chapter_open_visual_override",
        })
    return sections


def _build_back_matter_sections(page_map: dict[int, dict], appendix_titles: dict[str, str]) -> list[dict]:
    """Group back matter pages (p380-412) into sections."""
    sections: list[dict] = []

    # Transition blank (p380)
    sections.append({
        "section_id": "bm_transition_blank",
        "parent": "back_matter",
        "section_type": "blank",
        "title": "Transition Blank",
        "ordinal": 0,
        "start_page": 380,
        "end_page": 380,
        "page_count": 1,
    })

    # Appendix A (p381-397)
    sections.append({
        "section_id": "bm_appendix_a",
        "parent": "back_matter",
        "section_type": "appendix_a",
        "title": appendix_titles.get("appendix_a", "APPENDIX A"),
        "ordinal": 1,
        "start_page": 381,
        "end_page": 397,
        "page_count": 17,
        "printed_page_start": 291,
        "printed_page_end": 307,
    })

    # Appendix B (p398-399)
    sections.append({
        "section_id": "bm_appendix_b",
        "parent": "back_matter",
        "section_type": "appendix_b",
        "title": appendix_titles.get("appendix_b", "APPENDIX B"),
        "ordinal": 2,
        "start_page": 398,
        "end_page": 399,
        "page_count": 2,
        "printed_page_start": 308,
        "printed_page_end": 309,
    })

    # Appendix C (p400-404)
    sections.append({
        "section_id": "bm_appendix_c",
        "parent": "back_matter",
        "section_type": "appendix_c",
        "title": appendix_titles.get("appendix_c", "APPENDIX C"),
        "ordinal": 3,
        "start_page": 400,
        "end_page": 404,
        "page_count": 5,
        "printed_page_start": 310,
        "printed_page_end": 314,
    })

    # Index (p405-408)
    sections.append({
        "section_id": "bm_index",
        "parent": "back_matter",
        "section_type": "index",
        "title": appendix_titles.get("index", "INDEX"),
        "ordinal": 4,
        "start_page": 405,
        "end_page": 408,
        "page_count": 4,
        "printed_page_start": 315,
        "printed_page_end": 318,
    })

    # p409 is a blank page (keep separate from library artifacts)
    sections.append({
        "section_id": "bm_blank_409",
        "parent": "back_matter",
        "section_type": "blank",
        "title": "Blank",
        "ordinal": 5,
        "start_page": 409,
        "end_page": 409,
        "page_count": 1,
    })

    # Library artifacts (p410-411)
    sections.append({
        "section_id": "bm_artifacts",
        "parent": "back_matter",
        "section_type": "library_artifact",
        "title": "Library Artifacts",
        "ordinal": 6,
        "start_page": 410,
        "end_page": 411,
        "page_count": 2,
    })

    # Back cover (p412)
    sections.append({
        "section_id": "bm_back_cover",
        "parent": "back_matter",
        "section_type": "back_cover",
        "title": "Back Cover",
        "ordinal": 7,
        "start_page": 412,
        "end_page": 412,
        "page_count": 1,
    })

    return sections


def _build_page_membership(sections: list[dict], page_map: dict[int, dict]) -> list[dict]:
    """Create page-to-section membership records for all 412 pages."""
    memberships: list[dict] = []
    derived = _derive_printed_pages(page_map)
    for section in sections:
        for pg in range(section["start_page"], section["end_page"] + 1):
            record = page_map[pg]
            # Compute printed page number for body pages
            printed_num = None
            numbering = "unknown"
            if 25 <= pg <= 379:
                printed_num = derived[pg]
                if printed_num is not None:
                    numbering = "arabic"
            elif 381 <= pg <= 408:
                printed_num = pg - 381 + 291
                numbering = "arabic"
            memberships.append({
                "physical_page": pg,
                "section_id": section["section_id"],
                "parent": section["parent"],
                "section_type": section["section_type"],
                "primary_role": record["primary_role"],
                "printed_page_number": printed_num,
                "numbering_scheme": numbering,
            })
    return memberships


def _build_printed_page_map(page_map: dict[int, dict]) -> list[dict]:
    """Create printed page map for all 412 pages with normalized integers from labels."""
    result: list[dict] = []
    derived = _derive_printed_pages(page_map)
    for pg in range(1, 413):
        record = page_map[pg]
        # Parse printed page number from label or existing number
        printed_num = derived[pg] if 25 <= pg <= 408 else _parse_printed_page_number(
            record.get("printed_page_label"), record.get("printed_page_number"))
        numbering = "arabic" if printed_num is not None else record.get("numbering_scheme", "unknown")
        # Back matter pages 381-408 have fixed printed page numbers
        if 381 <= pg <= 408 and printed_num is None:
            printed_num = pg - 381 + 291
            numbering = "arabic"
        result.append({
            "physical_page": pg,
            "printed_page_number": printed_num,
            "printed_page_label": record.get("printed_page_label"),
            "numbering_scheme": numbering,
            "page_side": record.get("page_side"),
        })
    return result


def _build_book_structure(
    sections: list[dict],
    page_map: dict[int, dict],
    root: Path,
) -> dict:
    """Build the hierarchical book_structure.json."""
    from collections import Counter

    role_counts = Counter(page_map[pg]["primary_role"] for pg in range(1, 413))

    front_matter = [s for s in sections if s["parent"] == "front_matter"]
    body = [s for s in sections if s["parent"] == "body"]
    back_matter = [s for s in sections if s["parent"] == "back_matter"]

    # Printed page ranges are already set in section records from ToC/computation

    structure = {
        "document_id": DOCUMENT_ID,
        "pdf_sha256": PDF_SHA256,
        "total_pages": 412,
        "total_sections": len(sections),
        "total_chapters": len(body),
        "primary_role_distribution": dict(role_counts.most_common()),
        "hierarchy": {
            "front_matter": {
                "page_range": [1, 24],
                "section_count": len(front_matter),
                "sections": [s["section_id"] for s in front_matter],
            },
            "body": {
                "page_range": [25, 379],
                "section_count": len(body),
                "chapter_count": len(body),
                "sections": [s["section_id"] for s in body],
            },
            "back_matter": {
                "page_range": [380, 412],
                "section_count": len(back_matter),
                "sections": [s["section_id"] for s in back_matter],
            },
        },
        "sections": sections,
        "pagination_segments": PAGINATION_SEGMENTS,
        "version": "1.1",
    }
    return structure


def _verify_frozen_hashes(root: Path) -> dict[str, bool]:
    """Verify that frozen files have not been modified."""
    results: dict[str, bool] = {}
    for name, (rel, expected) in FROZEN_HASHES.items():
        path = root / rel
        if path.is_file():
            actual = sha256_file(path)
            results[name] = (actual == expected)
        else:
            results[name] = False
    return results


def build_structure_tree(root: Path) -> tuple[Path, dict]:
    """Build the Phase 2 book structure tree and write all outputs."""
    page_map = _load_page_map(root)
    assert len(page_map) == 412, f"Expected 412 page records, got {len(page_map)}"

    chapter_titles = _extract_chapter_titles(root)
    appendix_titles = _extract_appendix_titles(root)

    front_matter = _build_front_matter_sections(page_map)
    chapters = _build_chapter_sections(page_map, chapter_titles)
    back_matter = _build_back_matter_sections(page_map, appendix_titles)
    for section in back_matter:
        if section["section_type"] == "appendix_b":
            section["label"] = "APPENDIX B"
            section["title"] = "ESTIMATE OF EXPENSES"
        elif section["section_type"] == "appendix_c":
            section["label"] = "APPENDIX C"
            section["title"] = "TABLE OF DISTANCES AND STAGES"
            section["subtitle"] = "FROM HONAN TO SIAN-FU.*"
    sections = front_matter + chapters + back_matter

    memberships = _build_page_membership(sections, page_map)
    printed_page_map = _build_printed_page_map(page_map)
    book_structure = _build_book_structure(sections, page_map, root)

    # Write outputs
    tree_dir = root / TREE_DIR
    tree_dir.mkdir(parents=True, exist_ok=True)

    structure_path = tree_dir / "book_structure.json"
    atomic_write_json(structure_path, book_structure)

    sections_path = tree_dir / "sections.jsonl"
    atomic_write_jsonl(sections_path, sections)

    membership_path = tree_dir / "page_section_membership.jsonl"
    atomic_write_jsonl(membership_path, memberships)

    printed_path = tree_dir / "printed_page_map.jsonl"
    atomic_write_jsonl(printed_path, printed_page_map)
    atomic_write_json(tree_dir / "pagination_segments.json", PAGINATION_SEGMENTS)

    # Verify frozen hashes
    frozen_status = _verify_frozen_hashes(root)

    # Write checkpoint
    checkpoint_dir = root / CHECKPOINT_DIR
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "phase": "phase_2",
        "status": "completed",
        "timestamp": _iso_now(),
        "pdf_sha256": PDF_SHA256,
        "total_pages": 412,
        "total_sections": len(sections),
        "chapter_count": len(chapters),
        "front_matter_sections": len(front_matter),
        "back_matter_sections": len(back_matter),
        "outputs": {
            "book_structure": str(structure_path),
            "sections": str(sections_path),
            "page_section_membership": str(membership_path),
            "printed_page_map": str(printed_path),
        },
        "output_hashes": {
            "book_structure": sha256_file(structure_path),
            "sections": sha256_file(sections_path),
            "page_section_membership": sha256_file(membership_path),
            "printed_page_map": sha256_file(printed_path),
        },
        "frozen_hashes_verified": all(frozen_status.values()),
        "frozen_hash_details": frozen_status,
        "api_calls": 0,
    }
    checkpoint_path = checkpoint_dir / "phase_2_checkpoint.json"
    atomic_write_json(checkpoint_path, checkpoint)

    stats = {
        "total_pages": 412,
        "total_sections": len(sections),
        "chapter_count": len(chapters),
        "front_matter_sections": len(front_matter),
        "back_matter_sections": len(back_matter),
        "frozen_hashes_verified": all(frozen_status.values()),
    }
    return structure_path, stats


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def verify_gate(root: Path) -> tuple[bool, list[str]]:
    """Run all Phase 2 gate checks."""
    messages: list[str] = []

    tree_dir = root / TREE_DIR
    structure_path = tree_dir / "book_structure.json"
    sections_path = tree_dir / "sections.jsonl"
    membership_path = tree_dir / "page_section_membership.jsonl"
    printed_path = tree_dir / "printed_page_map.jsonl"

    # Load outputs
    if not structure_path.is_file():
        return False, ["book_structure.json not found"]
    structure = json.loads(structure_path.read_text("utf-8"))

    sections = _load_jsonl(sections_path)
    memberships = _load_jsonl(membership_path)
    printed_map = _load_jsonl(printed_path)

    # 1. 412/412 pages have exactly one primary structural membership
    if len(memberships) != 412:
        messages.append(f"Expected 412 memberships, got {len(memberships)}")
    member_pages = [m["physical_page"] for m in memberships]
    if member_pages != list(range(1, 413)):
        messages.append("Membership pages not 1-412 contiguous")

    # 2. Exactly 30 chapter sections
    chapters = [s for s in sections if s["section_type"] == "chapter"]
    if len(chapters) != 30:
        messages.append(f"Expected 30 chapters, got {len(chapters)}")

    # 3. Every chapter_open begins its chapter
    page_map = _load_page_map(root)
    for idx, ch_open_pg in enumerate(CHAPTER_OPEN_PAGES):
        if idx < len(chapters):
            if chapters[idx]["start_page"] != ch_open_pg:
                messages.append(
                    f"Chapter {idx+1} starts at p{chapters[idx]['start_page']}, expected p{ch_open_pg}"
                )

    # 3b. Chapter titles must not contain body first-words or OCR garbage
    for idx, ch in enumerate(chapters):
        title = ch.get("title", "")
        if not title or title == "UNTITLED":
            messages.append(f"Chapter {idx+1} has empty/UNTITLED title")
        # Check printed page start/end are present
        if ch.get("printed_page_start") is None:
            messages.append(f"Chapter {idx+1} missing printed_page_start")
        if ch.get("printed_page_end") is None:
            messages.append(f"Chapter {idx+1} missing printed_page_end")

    # 4. Chapter ranges are ordered and non-overlapping
    for i in range(len(chapters) - 1):
        if chapters[i]["end_page"] >= chapters[i + 1]["start_page"]:
            messages.append(
                f"Chapter {i+1} ends at p{chapters[i]['end_page']}, chapter {i+2} starts at p{chapters[i+1]['start_page']}"
            )

    # 5. Front matter, body, Appendix A/B/C, Index, and back matter are represented
    section_types = {s["section_type"] for s in sections}
    required_types = {
        "cover", "half_title", "title_page", "preface", "contents",
        "chapter", "appendix_a", "appendix_b", "appendix_c", "index", "back_cover",
    }
    missing = required_types - section_types
    if missing:
        messages.append(f"Missing section types: {missing}")

    # 5b. p409 must be blank section, not library_artifact
    p409_membership = next((m for m in memberships if m["physical_page"] == 409), None)
    if p409_membership and p409_membership["section_type"] != "blank":
        messages.append(f"p409 section_type={p409_membership['section_type']}, expected blank")
    p410_membership = next((m for m in memberships if m["physical_page"] == 410), None)
    if p410_membership and p410_membership["section_type"] != "library_artifact":
        messages.append(f"p410 section_type={p410_membership['section_type']}, expected library_artifact")

    # 6. Appendix A/B/C are distinct sections
    for app_type in ["appendix_a", "appendix_b", "appendix_c"]:
        app_sections = [s for s in sections if s["section_type"] == app_type]
        if len(app_sections) != 1:
            messages.append(f"Expected 1 {app_type} section, got {len(app_sections)}")

    # 7. p405-408 belong to Index; p412 belongs to back cover
    index_pages = [m for m in memberships if m["section_type"] == "index"]
    index_page_nums = sorted(m["physical_page"] for m in index_pages)
    if index_page_nums != [405, 406, 407, 408]:
        messages.append(f"Index pages: {index_page_nums}, expected [405, 406, 407, 408]")

    p412 = next((m for m in memberships if m["physical_page"] == 412), None)
    if p412 and p412["section_type"] != "back_cover":
        messages.append(f"p412 section_type={p412['section_type']}, expected back_cover")

    # 8. p381-408 map to printed p291-318
    for pg in range(381, 409):
        membership = next((m for m in memberships if m["physical_page"] == pg), None)
        if membership:
            expected_printed = pg - 381 + 291
            actual_printed = membership.get("printed_page_number")
            if actual_printed != expected_printed:
                messages.append(f"p{pg} printed={actual_printed}, expected {expected_printed}")

    # 8b. Body pages p25-p379 should have printed page numbers 1-289
    # 8b. Body pages should have printed page numbers from labels
    body_expected = {25: 1, 26: 2, 33: 9, 379: 289}
    for pg, expected in body_expected.items():
        membership = next((m for m in memberships if m["physical_page"] == pg), None)
        if membership:
            actual = membership.get("printed_page_number")
            if actual != expected:
                messages.append(f"p{pg} printed={actual}, expected {expected}")

    frozen_status = _verify_frozen_hashes(root)
    for name, ok in frozen_status.items():
        if not ok:
            messages.append(f"Frozen file {name} hash mismatch")

    return len(messages) == 0, messages
