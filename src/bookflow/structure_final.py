"""Phase 1 Final: Apply manual PDF audit overrides to produce versioned final structure.

Reads:
- Phase 1B page_map.jsonl (412 records, frozen baseline)
- Phase 1D merge_preview.jsonl (visual results for 58 targets)
- Manual override JSONL (153 records from human PDF audit)

Produces:
- data/fullbook/structure/final/page_map.jsonl (412 records with overrides applied)
- data/fullbook/structure/final/book_manifest.json
- data/fullbook/structure/final/section_candidates.jsonl

Priority: manual_pdf_audit > visual_api > cached_vision > offline_heuristic

Does NOT modify:
- Phase 1B page_map.jsonl (original)
- Phase 1D raw/normalized/ledger/merge_preview
- bridge_candidates.jsonl
- boundaries
- logical blocks
- translation cache
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file
from .structure_schemas import (
    ArtifactOverlay,
    BlankKind,
    BlankPageDetail,
    BridgeEligibility,
    ContentFeature,
    PrimaryRole,
    RenderingPolicy,
    StructurePageRecord,
    TextFlowRole,
)

PHASE1_FINAL_BASE = "data/fullbook/structure/final"
PHASE1B_PAGE_MAP = "data/fullbook/structure/registry/page_map.jsonl"
PHASE1D_MERGE_PREVIEW = "data/fullbook/structure/phase1d/fullbook_structure_merge_preview.jsonl"
MANUAL_OVERRIDES = "data/fullbook/structure/phase1d/manual_audit/phase1d_412_page_manual_overrides.jsonl"
MANUAL_CORE_VIEW = "data/fullbook/structure/phase1d/manual_audit/phase1d_412_page_corrected_core_view.jsonl"

# Valid content feature values (from the schema enum)
_VALID_CONTENT_FEATURES = {
    "prose", "heading", "caption", "quotation", "poetry",
    "list", "illustration", "map", "table", "index_entries",
    "page_number", "running_header", "footnote", "marginalia",
    "watermark", "library_stamp",
}

# Values that should be artifact_overlays, not content_features
_OVERLAY_MISPLACED = {"barcode", "binding_shadow", "scan_artifact", "overexposure", "underexposure"}


def _sanitize_content_features(record: dict) -> dict:
    """Move misplaced artifact values from content_features to artifact_overlays."""
    result = dict(record)
    cf = list(result.get("content_features", []))
    ao = list(result.get("artifact_overlays", []))
    cleaned_cf = []
    for item in cf:
        if item in _OVERLAY_MISPLACED:
            if item not in ao:
                ao.append(item)
        else:
            cleaned_cf.append(item)
    result["content_features"] = cleaned_cf
    result["artifact_overlays"] = ao
    return result


# Fields that manual overrides can set
_OVERRIDE_FIELDS = (
    "primary_role",
    "content_features",
    "artifact_overlays",
    "original_book_content",
    "contains_prose",
    "requires_region_analysis",
    "printed_page_label",
    "printed_page_number",
    "numbering_scheme",
    "page_side",
)

# Fields from the visual API results that should be applied to non-overridden visual target pages
_VISUAL_RESULT_FIELDS = (
    "primary_role",
    "content_features",
    "artifact_overlays",
    "original_book_content",
    "contains_prose",
    "requires_region_analysis",
    "printed_page_label",
    "printed_page_number",
    "numbering_scheme",
    "page_side",
)


def _load_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _load_overrides(root: Path) -> dict[int, dict]:
    path = root / MANUAL_OVERRIDES
    if not path.is_file():
        return {}
    overrides: dict[int, dict] = {}
    for record in _load_jsonl(path):
        pg = record["physical_page"]
        overrides[pg] = record.get("override", {})
    return overrides


def _load_visual_results(root: Path) -> dict[int, dict]:
    """Load visual API results from the merge preview."""
    path = root / PHASE1D_MERGE_PREVIEW
    if not path.is_file():
        return {}
    visual: dict[int, dict] = {}
    for record in _load_jsonl(path):
        if record.get("is_visual_target") and record.get("visual_status") == "success":
            pg = record["physical_page"]
            proposed = record.get("proposed_value", {})
            if proposed:
                visual[pg] = proposed
    return visual


def _derive_text_flow_role(primary_role: str, contains_prose: bool) -> str:
    """Derive text_flow_role from primary_role per the design spec."""
    if primary_role in ("chapter_body", "chapter_open"):
        return "prose_anchor" if primary_role == "chapter_open" else "prose_continuation"
    if primary_role in ("preface", "appendix"):
        return "prose_continuation" if contains_prose else "text_flow_none"
    if primary_role in ("blank", "full_page_illustration", "map"):
        return "non_prose_bridge"
    if primary_role in (
        "cover", "half_title", "frontispiece", "title_page",
        "digitization_notice", "dedication", "contents",
        "list_of_illustrations", "table", "index",
        "library_artifact", "back_cover", "unknown",
    ):
        return "structural_break"
    return "text_flow_none"


def _derive_bridge_eligibility(primary_role: str, blank_kind: str | None) -> str:
    """Derive bridge_eligibility per the design spec."""
    if primary_role == "chapter_body":
        return "not_applicable"
    if primary_role in ("full_page_illustration", "map"):
        return "bridge_capable"
    if primary_role == "blank":
        if blank_kind == "unknown_blank":
            return "bridge_blocking"
        return "bridge_capable"
    if primary_role in (
        "cover", "half_title", "frontispiece", "title_page",
        "digitization_notice", "dedication", "contents",
        "list_of_illustrations", "appendix", "table", "index",
        "library_artifact", "back_cover", "unknown",
    ):
        return "bridge_blocking"
    if primary_role == "preface":
        return "bridge_blocking"
    return "bridge_blocking"


def _derive_rendering_policy(primary_role: str) -> dict:
    """Derive rendering_policy per the design spec."""
    if primary_role in ("chapter_body", "chapter_open", "preface"):
        return {
            "preserve_page_record": True,
            "include_in_default_output": True,
            "include_in_text_flow": True,
            "include_in_book_element_order": True,
        }
    if primary_role in ("full_page_illustration", "map", "table"):
        return {
            "preserve_page_record": True,
            "include_in_default_output": True,
            "include_in_text_flow": False,
            "include_in_book_element_order": True,
        }
    if primary_role in (
        "title_page", "contents", "list_of_illustrations",
        "appendix", "index", "half_title", "frontispiece",
        "dedication", "cover",
    ):
        return {
            "preserve_page_record": True,
            "include_in_default_output": True,
            "include_in_text_flow": False,
            "include_in_book_element_order": True,
        }
    if primary_role == "blank":
        return {
            "preserve_page_record": True,
            "include_in_default_output": False,
            "include_in_text_flow": False,
            "include_in_book_element_order": False,
        }
    # library_artifact, digitization_notice, back_cover, unknown
    return {
        "preserve_page_record": True,
        "include_in_default_output": False,
        "include_in_text_flow": False,
        "include_in_book_element_order": False,
    }


def _derive_content_bearing(primary_role: str, contains_prose: bool) -> bool:
    """Derive content_bearing: formal book content pages are content-bearing."""
    if primary_role in (
        "chapter_body", "chapter_open", "preface", "appendix",
        "index", "table", "contents", "list_of_illustrations",
        "title_page", "half_title", "frontispiece", "dedication",
        "full_page_illustration", "map",
    ):
        return True
    if primary_role == "cover":
        return True
    if contains_prose:
        return True
    return False


def _build_blank_detail(blank_kind: str | None) -> dict | None:
    if blank_kind is None:
        return None
    return {
        "blank_kind": blank_kind,
        "visual_blank_score": 0.95,
        "ocr_text_length": 0,
        "ink_coverage": 0.0,
        "edge_density": 0.0,
        "known_watermark_only": blank_kind == "watermark_only_blank",
        "blank_confidence": 0.9,
        "blank_decision_source": "manual",
        "blank_requires_visual_confirmation": False,
    }


def _apply_override(record: dict, override: dict) -> dict:
    """Apply manual override fields to a page record."""
    result = dict(record)
    for field in _OVERRIDE_FIELDS:
        if field in override:
            result[field] = override[field]
    # Handle blank_kind: only store in blank_detail, not as a top-level field
    blank_kind = override.get("blank_kind")
    if blank_kind is not None:
        result["blank_detail"] = _build_blank_detail(blank_kind)
    elif override.get("primary_role") == "blank" and not override.get("blank_kind"):
        # Keep existing blank_detail if blank but no new blank_kind
        pass
    elif override.get("primary_role") != "blank":
        # Clear blank_detail if role is no longer blank
        result["blank_detail"] = None
    # Set classification_source
    result["classification_source"] = "manual"
    # Sanitize: move misplaced artifact values from content_features to artifact_overlays
    result = _sanitize_content_features(result)
    return result


def _apply_visual_result(record: dict, visual: dict) -> dict:
    """Apply visual API result fields to a page record."""
    result = dict(record)
    for field in _VISUAL_RESULT_FIELDS:
        if field in visual and visual[field] is not None:
            result[field] = visual[field]
    result["classification_source"] = "vision_api"
    # Update blank_detail
    blank_kind = visual.get("blank_kind")
    if blank_kind:
        result["blank_detail"] = _build_blank_detail(blank_kind)
    # Sanitize: move misplaced artifact values from content_features to artifact_overlays
    result = _sanitize_content_features(result)
    return result


def _finalize_record(record: dict) -> dict:
    """Derive dependent fields and ensure consistency."""
    result = dict(record)
    primary_role = result.get("primary_role", "unknown")
    contains_prose = result.get("contains_prose", False)
    blank_kind = None
    if result.get("blank_detail"):
        blank_kind = result["blank_detail"].get("blank_kind")

    # Derive text_flow_role
    result["text_flow_role"] = _derive_text_flow_role(primary_role, contains_prose)
    # Derive bridge_eligibility
    result["bridge_eligibility"] = _derive_bridge_eligibility(primary_role, blank_kind)
    # Derive rendering_policy
    result["rendering_policy"] = _derive_rendering_policy(primary_role)
    # Derive content_bearing
    result["content_bearing"] = _derive_content_bearing(primary_role, contains_prose)
    # Derive requires_followup
    result["requires_followup"] = result.get("requires_region_analysis", False)
    # Ensure confidence_by_field has at least primary_role
    if not result.get("confidence_by_field"):
        result["confidence_by_field"] = {"primary_role": 0.9} if result.get("classification_source") == "manual" else {"primary_role": 0.5}
    # Ensure evidence is a list
    if not isinstance(result.get("evidence"), list):
        result["evidence"] = []
    return result


def generate_final_page_map(root: Path) -> tuple[Path, dict]:
    """Generate the Phase 1 Final page_map.jsonl with manual overrides applied.

    Returns (output_path, stats_dict).
    """
    # Load sources
    page_map_records = {r["physical_page"]: r for r in _load_jsonl(root / PHASE1B_PAGE_MAP)}
    overrides = _load_overrides(root)
    visual_results = _load_visual_results(root)

    # Build final records
    final_records: list[dict] = []
    override_applied = 0
    visual_applied = 0
    phase1b_kept = 0

    for pg in range(1, 413):
        record = dict(page_map_records.get(pg))
        if not record:
            raise ValueError(f"Page {pg} missing from Phase 1B page_map")

        override = overrides.get(pg)
        if override:
            record = _apply_override(record, override)
            override_applied += 1
        elif pg in visual_results:
            record = _apply_visual_result(record, visual_results[pg])
            visual_applied += 1
        else:
            phase1b_kept += 1

        record = _finalize_record(record)
        final_records.append(record)

    # Validate all records against schema
    for record in final_records:
        StructurePageRecord.model_validate(record)

    # Write output
    output_dir = root / PHASE1_FINAL_BASE
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "page_map.jsonl"
    atomic_write_jsonl(output_path, final_records)

    stats = {
        "total_pages": len(final_records),
        "override_applied": override_applied,
        "visual_applied": visual_applied,
        "phase1b_kept": phase1b_kept,
    }
    return output_path, stats


def generate_section_candidates(root: Path) -> Path:
    """Generate section_candidates.jsonl from the final page_map.

    A section candidate is a contiguous run of pages with the same
    primary_role or related roles (e.g., chapter_open followed by chapter_body).
    """
    final_path = root / PHASE1_FINAL_BASE / "page_map.jsonl"
    records = _load_jsonl(final_path)

    # Group into sections
    sections: list[dict] = []
    current_section: dict | None = None
    section_idx = 0

    # Section roles that start a new section
    SECTION_START_ROLES = {
        "cover", "half_title", "frontispiece", "title_page",
        "digitization_notice", "dedication", "preface",
        "contents", "list_of_illustrations", "map",
        "chapter_open", "full_page_illustration",
        "appendix", "index", "library_artifact", "back_cover",
    }

    for record in records:
        pg = record["physical_page"]
        role = record["primary_role"]

        if role in SECTION_START_ROLES and role != "blank":
            # Start a new section
            if current_section:
                sections.append(current_section)
            section_idx += 1
            current_section = {
                "section_id": f"sec_{section_idx:03d}",
                "section_type": role,
                "start_page": pg,
                "end_page": pg,
                "page_count": 1,
                "primary_role": role,
            }
        elif role == "chapter_body":
            # Continue current chapter section if exists
            if current_section and current_section["section_type"] in ("chapter_open", "chapter_body"):
                current_section["end_page"] = pg
                current_section["page_count"] += 1
            else:
                # Orphan chapter_body - start a new section
                if current_section:
                    sections.append(current_section)
                section_idx += 1
                current_section = {
                    "section_id": f"sec_{section_idx:03d}",
                    "section_type": "chapter_body",
                    "start_page": pg,
                    "end_page": pg,
                    "page_count": 1,
                    "primary_role": "chapter_body",
                }
        elif role == "blank":
            # Blanks don't start sections but may be included
            if current_section:
                current_section["end_page"] = pg
                current_section["page_count"] += 1
            else:
                # Leading blank
                section_idx += 1
                current_section = {
                    "section_id": f"sec_{section_idx:03d}",
                    "section_type": "blank",
                    "start_page": pg,
                    "end_page": pg,
                    "page_count": 1,
                    "primary_role": "blank",
                }
        else:
            # Other roles - continue or start
            if current_section and current_section["section_type"] == role:
                current_section["end_page"] = pg
                current_section["page_count"] += 1
            else:
                if current_section:
                    sections.append(current_section)
                section_idx += 1
                current_section = {
                    "section_id": f"sec_{section_idx:03d}",
                    "section_type": role,
                    "start_page": pg,
                    "end_page": pg,
                    "page_count": 1,
                    "primary_role": role,
                }

    if current_section:
        sections.append(current_section)

    output_path = root / PHASE1_FINAL_BASE / "section_candidates.jsonl"
    atomic_write_jsonl(output_path, sections)
    return output_path


def generate_book_manifest(root: Path, stats: dict) -> Path:
    """Generate the Phase 1 Final book_manifest.json."""
    from collections import Counter

    final_path = root / PHASE1_FINAL_BASE / "page_map.jsonl"
    records = _load_jsonl(final_path)

    role_counts = Counter(r["primary_role"] for r in records)
    blank_kind_counts = Counter(
        r["blank_detail"]["blank_kind"] for r in records
        if r.get("blank_detail")
    )

    # Frozen file SHAs
    frozen_files = {
        "page_map_phase1b": "data/fullbook/structure/registry/page_map.jsonl",
        "bridge_candidates": "data/fullbook/structure/bridges/bridge_candidates.jsonl",
        "boundaries": "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
    }
    frozen_shas = {}
    for name, rel in frozen_files.items():
        path = root / rel
        frozen_shas[name] = sha256_file(path) if path.is_file() else "FILE_NOT_FOUND"

    # Compute final page_map SHA
    final_sha = sha256_file(final_path)

    manifest = {
        "phase": "phase_1_final",
        "version": "1.0",
        "document_id": "doc_78137e1bd662e86b",
        "pdf_sha256": "78137e1bd662e86b70cb1f197065e155fe003259c2e0244278221b4088990020",
        "total_pages": 412,
        "final_page_map_ref": "data/fullbook/structure/final/page_map.jsonl",
        "final_page_map_sha256": final_sha,
        "manual_audit_applied": True,
        "override_records_loaded": stats["override_applied"],
        "visual_results_applied": stats["visual_applied"],
        "phase1b_values_kept": stats["phase1b_kept"],
        "primary_role_distribution": dict(role_counts.most_common()),
        "blank_kind_distribution": dict(blank_kind_counts.most_common()),
        "chapter_open_count": role_counts.get("chapter_open", 0),
        "plate_verso_blank_count": blank_kind_counts.get("plate_verso_blank", 0),
        "frozen_files_sha256": frozen_shas,
        "boundaries_modified": False,
        "logical_blocks_modified": False,
        "translation_cache_modified": False,
        "api_calls": 0,
        "api_key_logged": False,
        "data_url_persisted": False,
    }

    output_path = root / PHASE1_FINAL_BASE / "book_manifest.json"
    atomic_write_json(output_path, manifest)
    return output_path


def verify_gate(root: Path) -> tuple[bool, list[str]]:
    """Run all Phase 1 Final gate checks."""
    final_path = root / PHASE1_FINAL_BASE / "page_map.jsonl"
    records = _load_jsonl(final_path)
    messages: list[str] = []

    # 1. Exactly 412 records
    if len(records) != 412:
        messages.append(f"Expected 412 records, got {len(records)}")
        return False, messages

    # 2. Pages 1-412, no gaps, no duplicates
    pages = [r["physical_page"] for r in records]
    if pages != list(range(1, 413)):
        messages.append(f"Pages not 1-412 contiguous: first={pages[:5]}, last={pages[-5:]}")
        return False, messages

    # 3. All records pass schema validation
    for record in records:
        try:
            StructurePageRecord.model_validate(record)
        except Exception as exc:
            messages.append(f"Schema validation failed for p{record['physical_page']}: {exc}")
            return False, messages

    # 4. 30 chapter_open pages
    chapter_open_pages = [r["physical_page"] for r in records if r["primary_role"] == "chapter_open"]
    if len(chapter_open_pages) != 30:
        messages.append(f"Expected 30 chapter_open, got {len(chapter_open_pages)}: {chapter_open_pages}")
    else:
        expected_chapter_open = {25, 33, 37, 46, 58, 67, 83, 93, 101, 113, 129, 136, 147, 156, 169, 182, 195, 206, 221, 230, 242, 263, 280, 292, 301, 314, 330, 350, 366, 374}
        if set(chapter_open_pages) != expected_chapter_open:
            messages.append(f"chapter_open pages mismatch: {sorted(chapter_open_pages)}")

    # 5. 33 plate_verso_blank pages
    plate_verso_pages = [
        r["physical_page"] for r in records
        if r.get("blank_detail") and r["blank_detail"].get("blank_kind") == "plate_verso_blank"
    ]
    if len(plate_verso_pages) != 33:
        messages.append(f"Expected 33 plate_verso_blank, got {len(plate_verso_pages)}: {plate_verso_pages}")
    else:
        expected_plate_verso = {44, 66, 90, 98, 104, 110, 116, 120, 124, 174, 184, 188, 198, 204, 208, 214, 220, 232, 244, 248, 256, 262, 270, 278, 288, 312, 324, 328, 334, 338, 340, 348, 354}
        if set(plate_verso_pages) != expected_plate_verso:
            messages.append(f"plate_verso_blank pages mismatch: {sorted(plate_verso_pages)}")

    # 6. Appendix A/B/C ranges
    appendix_pages = [r["physical_page"] for r in records if r["primary_role"] == "appendix"]
    expected_appendix = set(range(381, 405))  # p381-p404
    if set(appendix_pages) != expected_appendix:
        messages.append(f"Appendix pages mismatch: expected {sorted(expected_appendix)}, got {sorted(appendix_pages)}")

    # 7. Index range
    index_pages = [r["physical_page"] for r in records if r["primary_role"] == "index"]
    expected_index = {405, 406, 407, 408}
    if set(index_pages) != expected_index:
        messages.append(f"Index pages mismatch: expected {sorted(expected_index)}, got {sorted(index_pages)}")

    # 8. p387 = appendix with table in content_features
    p387 = next((r for r in records if r["physical_page"] == 387), None)
    if p387:
        if p387["primary_role"] != "appendix":
            messages.append(f"p387 primary_role={p387['primary_role']}, expected appendix")
        if "table" not in p387.get("content_features", []):
            messages.append(f"p387 content_features missing 'table': {p387.get('content_features')}")

    # 9. p400-p404 = appendix
    for pg in range(400, 405):
        r = next((r for r in records if r["physical_page"] == pg), None)
        if r and r["primary_role"] != "appendix":
            messages.append(f"p{pg} primary_role={r['primary_role']}, expected appendix")

    # 10. p412 = back_cover
    p412 = next((r for r in records if r["physical_page"] == 412), None)
    if p412 and p412["primary_role"] != "back_cover":
        messages.append(f"p412 primary_role={p412['primary_role']}, expected back_cover")

    # 11. Printed page numbers 291-318 for p381-p408
    for pg in range(381, 409):
        r = next((r for r in records if r["physical_page"] == pg), None)
        if r:
            expected_printed = pg - 381 + 291
            actual_printed = r.get("printed_page_number")
            if actual_printed != expected_printed:
                messages.append(f"p{pg} printed_page_number={actual_printed}, expected {expected_printed}")

    # 12. Frozen files unchanged
    expected_frozen = {
        "page_map_phase1b": "11115a628afb806267fd15f48731550705a938cd3f3d2fc87db82f295db2f5ad",
        "bridge_candidates": "169a214c10171a5fecaa758d066d541200c05d7c81be3dd7c059c58607ce814a",
        "boundaries": "b08c4bab8506f6d85cfd5e48b54ec801bd1868e10e6fb1375779011c08faf5a1",
    }
    for name, rel in {
        "page_map_phase1b": "data/fullbook/structure/registry/page_map.jsonl",
        "bridge_candidates": "data/fullbook/structure/bridges/bridge_candidates.jsonl",
        "boundaries": "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
    }.items():
        path = root / rel
        if path.is_file():
            actual = sha256_file(path)
            if actual != expected_frozen[name]:
                messages.append(f"Frozen file {name} SHA changed: expected {expected_frozen[name]}, got {actual}")

    return len(messages) == 0, messages
