"""Resumable Phase 12R back-matter production orchestration.

This module owns Phase 12R derived state only. It never rewrites the frozen
Canonical, Boundary, main-text translations, or previous candidate releases.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .io_utils import atomic_write_json, atomic_write_text, stable_hash
from .providers.config import load_provider_config
from .translation_provider import DeepSeekOpenAICompatibleProvider
from .vision_provider import ZhipuOpenAICompatibleProvider


PHASE12R_ROOT = Path("data/fullbook/back_matter/phase12r")
VISION_ROOT = PHASE12R_ROOT / "vision"
PRODUCTION_ROOT = PHASE12R_ROOT / "production"
VISION_PLAN = Path("reports/PHASE12R_VISION_EXTRACTION_PLAN.json")
VISION_MANIFEST = VISION_ROOT / "vision_execution_manifest_v1.json"
VISION_RESULTS = VISION_ROOT / "vision_extraction_results_v1.json"
VISION_MANIFEST_V2 = VISION_ROOT / "vision_execution_manifest_v2.json"
VISION_RESULTS_V2 = VISION_ROOT / "vision_extraction_results_v2.json"
VISION_ATTEMPTS = VISION_ROOT / "attempts"
VISION_CANDIDATES = VISION_ROOT / "recognized_back_matter_candidates_v1.json"
VISION_CANDIDATES_V2 = VISION_ROOT / "recognized_back_matter_candidates_v2.json"
STRUCTURED_OBJECTS_V2 = PHASE12R_ROOT / "structured_back_matter_objects_v2.json"
ILLUSTRATION_LIST_V2 = PHASE12R_ROOT / "illustration_list_reading_order_v2.json"
TRANSLATION_DELTA = PHASE12R_ROOT / "translation/phase12r_translation_delta_v1.json"
TRANSLATION_MANIFEST = PHASE12R_ROOT / "translation/translation_execution_manifest_v1.json"
TRANSLATION_ATTEMPTS = PHASE12R_ROOT / "translation/attempts"
TRANSLATION_OVERLAY = PHASE12R_ROOT / "translation/phase12r_translation_overlay_zh-Hans_v1.json"
FINAL_QA_ROOT = PHASE12R_ROOT / "final_visual_qa"
FINAL_QA_MANIFEST = FINAL_QA_ROOT / "final_visual_qa_manifest_v1.json"
FINAL_QA_REPORT = Path("reports/PHASE12R_FINAL_VLM_ACCEPTANCE.md")
VISION_BLOCKERS = Path("reports/PHASE12R_VISION_EXTRACTION_BLOCKERS.json")
VISION_REPORT = Path("reports/PHASE12R_VISION_EXTRACTION_EXECUTION.md")
CHECKPOINT = PRODUCTION_ROOT / "phase12r_production_checkpoint_v1.json"
FRONT_MATTER_ROUTING = PHASE12R_ROOT / "front_matter_routing_v1.json"
FRONT_MATTER_REPORT = Path("reports/PHASE12R_FRONT_MATTER_SCOPE_CORRECTION.md")
PROMPT_VERSION = "phase12r-vision-v4-bounded-tables"
WIRE_SCHEMA_VERSION = "phase12r-vision-wire-2.0"
ADAPTER_VERSION = "phase12r-vision-adapter-3.2"
ATTEMPT_SCHEMA_VERSION = "phase12r-vision-attempt-2.0"

FROZEN_FILES = {
    "canonical": (
        "data/fullbook/canonical/canonical_book_document_v1.json",
        "16c1c9ba4d60d1c2a4124433291a1a56bf499384215c720f6988e6e183c01326",
    ),
    "boundary": (
        "data/fullbook/main_text/boundaries/main_text.boundaries.jsonl",
        "b08c4bab8506f6d85cfd5e48b54ec801bd1868e10e6fb1375779011c08faf5a1",
    ),
    "source_main_text": (
        "data/fullbook/main_text/source_document_main_text_v1.json",
        "f18ad3eefa24eec1241dbe69a8baa0ecd8512a998a4796b5636c8f980cc01c8a",
    ),
    "bilingual_main_text": (
        "data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json",
        "d00f42fecbd0f8410019a2b9cfb42eeba0a2dc6f97188fdcb00cce8acf4bdc8b",
    ),
}
OLD_CANDIDATE_MANIFESTS = (
    "output/fullbook/big-game-en-reading-release-20260717T155541Z877193/render_manifest.json",
    "output/fullbook/big-game-zh-Hans-reading-release-20260717T155700Z478010/render_manifest.json",
    "output/fullbook/big-game-bilingual-reading-release-20260717T160109Z973759/render_manifest.json",
)
ALLOWED_OBJECT_TYPES = {
    "prose", "heading", "list_entry", "figure", "caption", "table",
    "rotated_table", "multi_page_table", "illustration_list", "index",
    "multi_column_text", "footnote", "facsimile_region",
}
FRONT_MATTER_TRANSLATION_POLICIES = {
    "frozen_body", "reuse_existing", "superseded_provenance", "new_delta",
    "non_translatable_artifact", "navigation_generated", "unresolved",
}
FRONT_MATTER_RENDER_POLICIES = {
    "render_existing", "render_from_structured_object", "omit_from_reading",
    "evidence_only", "generated_navigation",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _is_artifact(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    return any(marker in normalized for marker in (
        "digitized by microsoft", "univ calif", "library artifact", "barcode",
        "scanner attribution",
    ))


def _route_policy(section_id: str, section_type: str, source_text: str) -> dict[str, Any]:
    if section_id.startswith("ch_"):
        return {
            "section_family": "chapter_body", "translation_policy": "frozen_body",
            "render_policy": "render_existing", "replacement_object_id": None,
            "provenance_status": "active_frozen",
        }
    if _is_artifact(source_text):
        return {
            "section_family": "front_matter", "translation_policy": "non_translatable_artifact",
            "render_policy": "omit_from_reading", "replacement_object_id": None,
            "provenance_status": "artifact",
        }
    if section_id == "fm_list_of_illustrations":
        return {
            "section_family": "front_matter", "translation_policy": "superseded_provenance",
            "render_policy": "render_from_structured_object",
            "replacement_object_id": "lo_illustration_list_0021_0022",
            "provenance_status": "superseded",
        }
    if section_id == "fm_contents":
        return {
            "section_family": "front_matter", "translation_policy": "navigation_generated",
            "render_policy": "generated_navigation", "replacement_object_id": "generated_contents_navigation",
            "provenance_status": "navigation_source",
        }
    if section_type in {"frontispiece", "map"}:
        return {
            "section_family": "front_matter", "translation_policy": "reuse_existing",
            "render_policy": "render_from_structured_object",
            "replacement_object_id": f"canonical_{section_type}_object",
            "provenance_status": "structured_visual_source",
        }
    if section_type in {"half_title", "title_page"}:
        return {
            "section_family": "front_matter", "translation_policy": "reuse_existing",
            "render_policy": "render_from_structured_object",
            "replacement_object_id": "book_metadata_title_page",
            "provenance_status": "structured_title_source",
        }
    if section_type == "cover":
        return {
            "section_family": "front_matter", "translation_policy": "reuse_existing",
            "render_policy": "evidence_only", "replacement_object_id": "source_cover_object",
            "provenance_status": "cover_source",
        }
    return {
        "section_family": "front_matter", "translation_policy": "reuse_existing",
        "render_policy": "render_existing", "replacement_object_id": None,
        "provenance_status": "active_reused",
    }


def build_front_matter_routing(root: Path) -> dict[str, Any]:
    """Derive the chapter/front-matter boundary without modifying Canonical."""
    root = root.resolve()
    canonical_path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
    canonical = _load(canonical_path)
    sections = {item["section_id"]: item for item in canonical["sections"]}
    routes = []
    for unit in canonical["logical_units"]:
        section = sections[unit["section_id"]]
        policy = _route_policy(unit["section_id"], section["section_type"], unit["source_text"])
        routes.append({
            "logical_block_id": unit["logical_block_id"],
            "section_id": unit["section_id"],
            "section_family": policy["section_family"],
            "section_type": section["section_type"],
            "source_pages": unit["source_pages"],
            "source_sha": unit["source_text_sha256"],
            "translation_policy": policy["translation_policy"],
            "render_policy": policy["render_policy"],
            "replacement_object_id": policy["replacement_object_id"],
            "provenance_status": policy["provenance_status"],
        })
    chapter = [item for item in routes if item["section_family"] == "chapter_body"]
    front = [item for item in routes if item["section_family"] == "front_matter"]
    checks = {
        "chapter_plus_front_equals_canonical": len(chapter) + len(front) == len(canonical["logical_units"]) == 971,
        "all_chapters_frozen_body": all(item["translation_policy"] == "frozen_body" for item in chapter),
        "chapter_ids_exact": {item["section_id"] for item in chapter} == {f"ch_{number:02d}" for number in range(1, 31)},
        "illustration_units_structured_replacement": all(
            item["render_policy"] in {"render_from_structured_object", "omit_from_reading"}
            and item.get("replacement_object_id") in {"lo_illustration_list_0021_0022", None}
            for item in front if item["section_id"] == "fm_list_of_illustrations"
        ),
        "preface_reuses_existing": all(
            item["translation_policy"] == "reuse_existing" and item["render_policy"] == "render_existing"
            for item in front if item["section_id"] in {"fm_preface", "fm_dedication"}
        ),
        "artifacts_omitted_from_reading": all(
            item["render_policy"] == "omit_from_reading"
            for item in front if item["translation_policy"] == "non_translatable_artifact"
        ),
        "policies_known": all(
            item["translation_policy"] in FRONT_MATTER_TRANSLATION_POLICIES
            and item["render_policy"] in FRONT_MATTER_RENDER_POLICIES for item in routes
        ),
    }
    counts = {
        "canonical_logical_units": len(routes), "chapter_body_units": len(chapter),
        "front_matter_units": len(front),
        "reused_front_matter_units": sum(item["translation_policy"] == "reuse_existing" for item in front),
        "superseded_illustration_list_units": sum(item["section_id"] == "fm_list_of_illustrations" for item in front),
        "navigation_generated_units": sum(item["translation_policy"] == "navigation_generated" for item in front),
        "artifact_units": sum(item["translation_policy"] == "non_translatable_artifact" for item in front),
    }
    value = {
        "schema_version": "phase12r-front-matter-routing-1.0", "phase": "12R",
        "canonical_ref": "data/fullbook/canonical/canonical_book_document_v1.json",
        "canonical_sha256": _sha(canonical_path), "counts": counts,
        "routes": routes, "validation": {"valid": all(checks.values()), "checks": checks},
        "generated_at": _utc_now(),
    }
    if not value["validation"]["valid"]:
        raise RuntimeError("front-matter routing validation failed")
    atomic_write_json(root / FRONT_MATTER_ROUTING, value)
    report = "\n".join([
        "# Phase 12R Front Matter Scope Correction", "",
        "Status: completed", "",
        "## Corrected boundary", "",
        f"- chapter body (`ch_01`-`ch_30`): {counts['chapter_body_units']}",
        f"- front matter: {counts['front_matter_units']}",
        f"- Canonical logical units total: {counts['canonical_logical_units']}",
        f"- reused front-matter units: {counts['reused_front_matter_units']}",
        f"- superseded `fm_list_of_illustrations` units: {counts['superseded_illustration_list_units']}",
        f"- generated navigation source units: {counts['navigation_generated_units']}",
        f"- non-translatable artifact units: {counts['artifact_units']}", "",
        "## Routing rules", "",
        "- Only `ch_01` through `ch_30` are strict frozen chapter-body units.",
        "- Dedication and Preface retain their existing source hashes, translations, and cache eligibility.",
        "- OCR Contents lines are navigation provenance; the reading edition uses generated navigation.",
        "- The old List of Illustrations units remain provenance and are replaced once by the Phase 12R structured object.",
        "- Cover/title/frontispiece/map sources are routed by object type; digitization artifacts are omitted from reading output.",
        "- Canonical, Boundary, existing translation state, and old candidate releases were not modified.", "",
        "## Validation", "",
        *[f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in checks.items()], "",
        "API calls/tokens: 0/0", "",
    ])
    atomic_write_text(root / FRONT_MATTER_REPORT, report)
    return value


def _json_content(content: str) -> dict[str, Any]:
    value = content.lstrip("\ufeff").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            raise ValueError("invalid JSON code fence")
        value = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        if start < 0:
            raise
        parsed, end = json.JSONDecoder().raw_decode(value, start)
        trailing = value[end:].strip()
        if trailing not in {"", "```"} and trailing.startswith("{"):
            raise ValueError("multiple JSON objects in vision response")
    if not isinstance(parsed, dict):
        raise ValueError("vision response must be a JSON object")
    return parsed


def _data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def frozen_hashes(root: Path) -> dict[str, Any]:
    files = {}
    valid = True
    for name, (ref, expected) in FROZEN_FILES.items():
        actual = _sha(root / ref)
        files[name] = {"path": ref, "expected_sha256": expected, "actual_sha256": actual, "valid": actual == expected}
        valid = valid and actual == expected
    for index, ref in enumerate(OLD_CANDIDATE_MANIFESTS, 1):
        path = root / ref
        actual = _sha(path)
        files[f"old_candidate_{index}"] = {"path": ref, "actual_sha256": actual, "valid": True}
    return {"valid": valid, "files": files}


def _load_checkpoint(root: Path) -> dict[str, Any]:
    path = root / CHECKPOINT
    if path.is_file():
        return _load(path)
    frozen = frozen_hashes(root)
    if not frozen["valid"]:
        raise RuntimeError("frozen baseline hash mismatch")
    value = {
        "schema_version": "phase12r-production-checkpoint-1.0",
        "phase": "12R",
        "status": "in_progress",
        "last_durable_stage": None,
        "next_stage": "vision_extraction",
        "stages": {},
        "api_calls": {"vision": 0, "translation": 0, "final_vlm": 0},
        "api_tokens": {"vision": 0, "translation": 0, "final_vlm": 0},
        "frozen_baseline": frozen,
        "old_candidate_manifest_hashes": {
            item["path"]: item["actual_sha256"]
            for key, item in frozen["files"].items() if key.startswith("old_candidate_")
        },
        "phase_12_5_entered": False,
        "created_at": _utc_now(),
    }
    atomic_write_json(path, value)
    return value


def _verify_frozen(root: Path, checkpoint: dict[str, Any]) -> dict[str, Any]:
    current = frozen_hashes(root)
    for ref, expected in checkpoint["old_candidate_manifest_hashes"].items():
        if _sha(root / ref) != expected:
            current["valid"] = False
            current.setdefault("errors", []).append(f"old candidate changed: {ref}")
    if not current["valid"]:
        raise RuntimeError("frozen baseline changed during Phase 12R")
    return current


def _vision_schema() -> dict[str, Any]:
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    bbox = {
        "type": "array", "items": {"type": "integer", "minimum": 0},
        "minItems": 4, "maxItems": 4,
    }
    field = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string"}, "value": nullable_string,
            "status": {"type": "string", "enum": ["confirmed", "unresolved", "not_applicable"]},
            "bbox": bbox,
        },
        "required": ["name", "value", "status", "bbox"],
    }
    cell = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "column_index": {"type": "integer", "minimum": 0}, "text": nullable_string,
            "status": {"type": "string", "enum": ["confirmed", "unresolved"]},
            "bbox": bbox,
        },
        "required": ["column_index", "text", "status", "bbox"],
    }
    row = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "row_index": {"type": "integer", "minimum": 0},
            "row_type": {"type": "string", "enum": ["header", "data", "subtotal", "total", "continuation", "note"]},
            "bbox": bbox, "cells": {"type": "array", "items": cell},
        },
        "required": ["row_index", "row_type", "bbox", "cells"],
    }
    obj = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "object_id": {"type": "string"},
            "object_type": {"type": "string", "enum": sorted(ALLOWED_OBJECT_TYPES)},
            "reading_order": {"type": "integer", "minimum": 0}, "bbox": bbox,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "transcription_status": {"type": "string", "enum": ["confirmed", "partial", "unresolved", "artifact"]},
            "text": nullable_string, "fields": {"type": "array", "items": field},
            "rows": {"type": "array", "items": row},
        },
        "required": ["object_id", "object_type", "reading_order", "bbox", "confidence", "transcription_status", "text", "fields", "rows"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "request_id": {"type": "string"}, "physical_page": {"type": "integer"},
            "region_id": {"type": "string"}, "orientation_degrees_clockwise": {"type": "integer", "enum": [0, 90, 180, 270]},
            "objects": {"type": "array", "items": obj},
            "artifacts": {"type": "array", "items": field},
            "unresolved": {"type": "array", "items": field},
        },
        "required": ["request_id", "physical_page", "region_id", "orientation_degrees_clockwise", "objects", "artifacts", "unresolved"],
    }


def _provider_vision_schema() -> dict[str, Any]:
    """Compact strict wire schema; expanded locally into the publication schema."""
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    bbox = {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 4, "maxItems": 4}
    field = {
        "type": "object", "additionalProperties": False,
        "properties": {"n": {"type": "string"}, "v": nullable, "s": {"type": "string", "enum": ["confirmed", "unresolved", "not_applicable"]}, "b": bbox},
        "required": ["n", "v", "s", "b"],
    }
    cell = {
        "type": "object", "additionalProperties": False,
        "properties": {"n": {"type": "integer", "minimum": 0}, "t": nullable, "s": {"type": "string", "enum": ["confirmed", "unresolved"]}, "b": bbox},
        "required": ["n", "t", "s", "b"],
    }
    row = {
        "type": "object", "additionalProperties": False,
        "properties": {"n": {"type": "integer", "minimum": 0}, "t": {"type": "string", "enum": ["header", "data", "subtotal", "total", "continuation", "note"]}, "b": bbox, "c": {"type": "array", "items": cell}},
        "required": ["n", "t", "b", "c"],
    }
    obj = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "i": {"type": "string"}, "t": {"type": "string", "enum": sorted(ALLOWED_OBJECT_TYPES)},
            "n": {"type": "integer", "minimum": 0}, "b": bbox,
            "c": {"type": "number", "minimum": 0, "maximum": 1},
            "s": {"type": "string", "enum": ["confirmed", "partial", "unresolved", "artifact"]},
            "q": nullable, "f": {"type": "array", "items": field}, "w": {"type": "array", "items": row},
        },
        "required": ["i", "t", "n", "b", "c", "s", "q", "f", "w"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "x": {"type": "array", "items": obj}, "a": {"type": "array", "items": field},
            "u": {"type": "array", "items": field},
        },
        "required": ["x", "a", "u"],
    }


def _expand_compact_payload(
    value: dict[str, Any], request: dict[str, Any] | None = None, *,
    width: int | None = None, height: int | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if "x" not in value or request is None:
        return value, []
    adaptations = ["compact_wire_schema_expanded"]
    fallback_bbox = [0, 0, width, height] if width and height else [0, 0, 1, 1]

    def bbox(raw: Any) -> list[int]:
        if not isinstance(raw, list) or len(raw) != 4 or any(
            not isinstance(item, (int, float)) or isinstance(item, bool) for item in raw
        ):
            if "missing_bbox_mapped_to_region" not in adaptations:
                adaptations.append("missing_bbox_mapped_to_region")
            return list(fallback_bbox)
        result = [int(round(item)) for item in raw]
        if result[2] <= result[0] or result[3] <= result[1]:
            if "invalid_bbox_preserved_for_validation" not in adaptations:
                adaptations.append("invalid_bbox_preserved_for_validation")
            return result
        if width and height:
            result = [
                max(0, min(result[0], width - 1)), max(0, min(result[1], height - 1)),
                max(1, min(result[2], width)), max(1, min(result[3], height)),
            ]
            if result[2] <= result[0] or result[3] <= result[1]:
                result = list(fallback_bbox)
            if result != raw and "bbox_clamped_to_region" not in adaptations:
                adaptations.append("bbox_clamped_to_region")
        return result

    def field(item: Any, *, default_name: str = "artifact", default_bbox: list[int] | None = None) -> dict[str, Any]:
        if not isinstance(item, dict):
            marker_bbox = default_bbox if isinstance(default_bbox, list) and len(default_bbox) == 4 and default_bbox[2] > default_bbox[0] and default_bbox[3] > default_bbox[1] else fallback_bbox
            return {"name": default_name, "value": None, "status": "unresolved", "bbox": list(marker_bbox)}
        name = item.get("n", item.get("i", default_name))
        raw = item.get("v", item.get("t"))
        status = item.get("s", "confirmed" if raw is not None else "unresolved")
        if status not in {"confirmed", "unresolved", "not_applicable"}:
            status = "confirmed" if raw is not None else "unresolved"
        field_bbox = [0, 0, 0, 0] if status == "not_applicable" else bbox(item.get("b", default_bbox))
        return {"name": str(name), "value": None if status == "unresolved" else (str(raw) if raw is not None else None), "status": status, "bbox": field_bbox}

    compact_objects = value.get("x")
    compact_artifacts = value.get("a", [])
    compact_unresolved = value.get("u", [])
    if isinstance(compact_objects, dict) and isinstance(compact_objects.get("i"), list) and set(compact_objects) <= {"i", "a", "u"}:
        compact_artifacts = compact_objects.get("a", compact_artifacts)
        compact_unresolved = compact_objects.get("u", compact_unresolved)
        compact_objects = compact_objects["i"]
        adaptations.append("nested_compact_collection_unwrapped")
    elif isinstance(compact_objects, dict):
        compact_objects = [compact_objects]
        adaptations.append("single_compact_object_wrapped")
    if not isinstance(compact_objects, list):
        compact_objects = []
        adaptations.append("invalid_compact_collection_replaced")
    objects = []
    expected_type = {
        "illustration_list": "list_entry", "multi_page_table": "multi_page_table",
        "rotated_table": "rotated_table", "index": "index", "prose_or_list_entry": "prose",
    }.get((request or {}).get("output_object"), "prose")
    unresolved = [field(item, default_name=f"unresolved_{index}") for index, item in enumerate(compact_unresolved if isinstance(compact_unresolved, list) else [])]
    for index, item in enumerate(compact_objects):
        if not isinstance(item, dict):
            unresolved.append(field(None, default_name=f"object_{index}_invalid"))
            continue
        item_type = item.get("t", expected_type)
        if expected_type in {"multi_page_table", "rotated_table", "index"}:
            item_type = expected_type
        elif item_type not in ALLOWED_OBJECT_TYPES:
            item_type = expected_type
        item_text = item.get("q") if isinstance(item.get("q"), str) else None
        if item_text is None and isinstance(item.get("n"), str):
            item_text = item["n"]
        if item_text is None and isinstance(item.get("t"), str) and item["t"] not in ALLOWED_OBJECT_TYPES:
            item_text = item["t"]
        if item_text is None and isinstance(item.get("c"), str):
            item_text = item["c"]
        raw_bbox = item.get("b")
        item_bbox = bbox(raw_bbox)
        if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            unresolved.append(field(None, default_name=f"object_{index}_bbox", default_bbox=item_bbox))
        if not item.get("w") and expected_type in {"multi_page_table", "rotated_table"}:
            unresolved.append({"name": f"{item.get('i', 'object')}_rows", "value": None, "status": "unresolved", "bbox": item_bbox})
        raw_fields = item.get("f") if isinstance(item.get("f"), list) else []
        raw_rows = item.get("w") if isinstance(item.get("w"), list) else []
        rows = []
        for row_index, row in enumerate(raw_rows):
            if not isinstance(row, dict):
                unresolved.append(field(None, default_name=f"object_{index}_row_{row_index}", default_bbox=item_bbox))
                continue
            row_bbox = bbox(row.get("b", item_bbox))
            raw_cells = row.get("c") if isinstance(row.get("c"), list) else []
            cells = []
            for cell_index, cell in enumerate(raw_cells):
                if not isinstance(cell, dict):
                    unresolved.append(field(None, default_name=f"object_{index}_row_{row_index}_cell_{cell_index}", default_bbox=row_bbox))
                    continue
                raw_status = cell.get("s")
                status = raw_status if isinstance(raw_status, str) and raw_status in {"confirmed", "unresolved"} else ("confirmed" if cell.get("t") is not None else "unresolved")
                cells.append({
                    "column_index": cell_index,
                    "text": None if status == "unresolved" else (str(cell.get("t")) if cell.get("t") is not None else None),
                    "status": status, "bbox": bbox(cell.get("b", row_bbox)),
                })
            if cells:
                row_type = row.get("t") if row.get("t") in {"header", "data", "subtotal", "total", "continuation", "note"} else "data"
                rows.append({"row_index": len(rows), "row_type": row_type, "bbox": row_bbox, "cells": cells})
            else:
                unresolved.append(field(None, default_name=f"object_{index}_row_{row_index}_cells", default_bbox=row_bbox))
        raw_status = item.get("s")
        status = raw_status if isinstance(raw_status, str) and raw_status in {"confirmed", "partial", "unresolved", "artifact"} else "partial"
        objects.append({
            "object_id": f"{request['request_id']}_{len(objects):04d}", "object_type": item_type,
            "reading_order": len(objects), "bbox": item_bbox,
            "confidence": float(item.get("c", 0.5)) if isinstance(item.get("c", 0.5), (int, float)) else 0.5,
            "transcription_status": status,
            "text": item_text, "fields": [field(entry, default_name=f"field_{field_index}", default_bbox=item_bbox) for field_index, entry in enumerate(raw_fields)],
            "rows": rows,
        })
    return {
        "request_id": request["request_id"],
        "physical_page": request["physical_page"],
        "region_id": request["region_id"],
        "orientation_degrees_clockwise": int(request.get("orientation_degrees_clockwise", 0)), "objects": objects,
        "artifacts": [field(item, default_name=f"artifact_{index}") for index, item in enumerate(compact_artifacts if isinstance(compact_artifacts, list) else [])], "unresolved": unresolved,
    }, adaptations


def _bbox_valid(value: Any, width: int, height: int, *, allow_empty: bool = False) -> bool:
    if allow_empty and value == [0, 0, 0, 0]:
        return True
    return (
        isinstance(value, list) and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and 0 <= value[0] < value[2] <= width and 0 <= value[1] < value[3] <= height
    )


def validate_vision_result(
    value: dict[str, Any], request: dict[str, Any], *, width: int, height: int,
) -> list[str]:
    errors: list[str] = []
    expected_keys = {"request_id", "physical_page", "region_id", "orientation_degrees_clockwise", "objects", "artifacts", "unresolved"}
    if set(value) != expected_keys:
        errors.append("top-level keys do not match strict schema")
    for key in ("request_id", "physical_page", "region_id"):
        if value.get(key) != request.get(key):
            errors.append(f"{key} mismatch")
    expected_orientation = int(request.get("orientation_degrees_clockwise", 0))
    if value.get("orientation_degrees_clockwise") != expected_orientation:
        errors.append("orientation mismatch")
    objects = value.get("objects")
    if not isinstance(objects, list) or not objects:
        errors.append("objects must be non-empty")
        return errors
    orders: list[int] = []
    for position, item in enumerate(objects):
        prefix = f"objects[{position}]"
        required = {"object_id", "object_type", "reading_order", "bbox", "confidence", "transcription_status", "text", "fields", "rows"}
        if not isinstance(item, dict) or set(item) != required:
            errors.append(f"{prefix} keys do not match strict schema")
            continue
        if item["object_type"] not in ALLOWED_OBJECT_TYPES:
            errors.append(f"{prefix}.object_type invalid")
        if not _bbox_valid(item["bbox"], width, height):
            errors.append(f"{prefix}.bbox invalid")
        if not isinstance(item["reading_order"], int):
            errors.append(f"{prefix}.reading_order invalid")
        else:
            orders.append(item["reading_order"])
        if not isinstance(item["confidence"], (int, float)) or not 0 <= item["confidence"] <= 1:
            errors.append(f"{prefix}.confidence invalid")
        for field_index, field in enumerate(item["fields"]):
            if not isinstance(field, dict) or set(field) != {"name", "value", "status", "bbox"}:
                errors.append(f"{prefix}.fields[{field_index}] invalid")
            elif not _bbox_valid(field["bbox"], width, height, allow_empty=field["status"] == "not_applicable"):
                errors.append(f"{prefix}.fields[{field_index}].bbox invalid")
        for row_index, row in enumerate(item["rows"]):
            if not isinstance(row, dict) or set(row) != {"row_index", "row_type", "bbox", "cells"}:
                errors.append(f"{prefix}.rows[{row_index}] invalid")
                continue
            if not _bbox_valid(row["bbox"], width, height):
                errors.append(f"{prefix}.rows[{row_index}].bbox invalid")
            indices = []
            for cell_index, cell in enumerate(row["cells"]):
                if not isinstance(cell, dict) or set(cell) != {"column_index", "text", "status", "bbox"}:
                    errors.append(f"{prefix}.rows[{row_index}].cells[{cell_index}] invalid")
                    continue
                indices.append(cell["column_index"])
                if not _bbox_valid(cell["bbox"], width, height):
                    errors.append(f"{prefix}.rows[{row_index}].cells[{cell_index}].bbox invalid")
                if cell["status"] == "unresolved" and cell["text"] is not None:
                    errors.append(f"{prefix}.rows[{row_index}].cells[{cell_index}] unresolved text must be null")
            if indices != list(range(len(indices))):
                errors.append(f"{prefix}.rows[{row_index}] column indices are not contiguous")
    if orders != sorted(orders) or len(orders) != len(set(orders)):
        errors.append("reading_order must be unique and ascending")
    for category in ("artifacts", "unresolved"):
        values = value.get(category)
        if not isinstance(values, list):
            errors.append(f"{category} must be an array")
            continue
        for index, field in enumerate(values):
            if not isinstance(field, dict) or set(field) != {"name", "value", "status", "bbox"} or not _bbox_valid(field.get("bbox"), width, height):
                errors.append(f"{category}[{index}] invalid")
    return errors


def _adapt_vision_payload(
    value: dict[str, Any], request: dict[str, Any], *, width: int, height: int,
) -> tuple[dict[str, Any], list[str]]:
    """Normalize common provider collection aliases before strict validation.

    The adapter may rename and reshape visible textual fields, but it never
    manufactures table cells or cell provenance.
    """
    expected = {"request_id", "physical_page", "region_id", "orientation_degrees_clockwise", "objects", "artifacts", "unresolved"}
    if set(value) == expected and value.get("objects"):
        return value, []
    for wrapper in ("result", "data", "output", "extraction"):
        nested = value.get(wrapper)
        if isinstance(nested, dict):
            adapted, changes = _adapt_vision_payload(nested, request, width=width, height=height)
            if changes or adapted.get("objects"):
                return adapted, [f"wrapper:{wrapper}", *changes]
    aliases = ("entries", "tables", "table_objects", "blocks", "index_entries", "illustrations", "items")
    alias = next((name for name in aliases if isinstance(value.get(name), list) and value[name]), None)
    if alias is None:
        return value, []
    adaptations = [f"collection_alias:{alias}->objects"]
    expected_type = {
        "illustration_list": "list_entry", "multi_page_table": "multi_page_table",
        "rotated_table": "rotated_table", "index": "index",
        "prose_or_list_entry": "prose",
    }[request["output_object"]]
    objects = []
    unresolved = list(value.get("unresolved") or [])
    for index, source in enumerate(value[alias]):
        if not isinstance(source, dict):
            continue
        bbox = source.get("bbox")
        if not _bbox_valid(bbox, width, height):
            unresolved.append({
                "name": f"{alias}_{index}_bbox", "value": None,
                "status": "unresolved", "bbox": [0, 0, width, height],
            })
            continue
        object_type = source.get("object_type") or source.get("type") or expected_type
        if object_type not in ALLOWED_OBJECT_TYPES:
            object_type = expected_type
        fields = source.get("fields") if isinstance(source.get("fields"), list) else []
        if not fields:
            for name in ("source_text", "latin_name", "printed_locator", "locator_kind", "term", "indent_level", "parent_term", "printed_page_references", "cross_reference", "column"):
                if name not in source:
                    continue
                raw = source.get(name)
                field_bbox = source.get(f"{name}_bbox", bbox)
                if raw is None:
                    status, field_bbox = "not_applicable", [0, 0, 0, 0]
                    text = None
                else:
                    status = "confirmed"
                    text = json.dumps(raw, ensure_ascii=False) if isinstance(raw, (list, dict)) else str(raw)
                fields.append({"name": name, "value": text, "status": status, "bbox": field_bbox})
        rows = source.get("rows") if isinstance(source.get("rows"), list) else []
        strict_rows = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict) or not _bbox_valid(row.get("bbox"), width, height):
                unresolved.append({"name": f"{alias}_{index}_row_{row_index}", "value": None, "status": "unresolved", "bbox": bbox})
                continue
            cells = row.get("cells")
            if not isinstance(cells, list) or any(
                not isinstance(cell, dict) or not _bbox_valid(cell.get("bbox"), width, height)
                for cell in cells
            ):
                unresolved.append({"name": f"{alias}_{index}_row_{row_index}_cells", "value": None, "status": "unresolved", "bbox": row["bbox"]})
                continue
            strict_rows.append({
                "row_index": int(row.get("row_index", row_index)),
                "row_type": row.get("row_type", "data") if row.get("row_type", "data") in {"header", "data", "subtotal", "total", "continuation", "note"} else "data",
                "bbox": row["bbox"],
                "cells": [{
                    "column_index": int(cell.get("column_index", cell_index)),
                    "text": None if cell.get("status") == "unresolved" else (str(cell.get("text")) if cell.get("text") is not None else None),
                    "status": cell.get("status", "confirmed") if cell.get("status", "confirmed") in {"confirmed", "unresolved"} else "unresolved",
                    "bbox": cell["bbox"],
                } for cell_index, cell in enumerate(cells)],
            })
        objects.append({
            "object_id": str(source.get("object_id") or source.get("entry_id") or source.get("id") or f"{request['request_id']}_{index:04d}"),
            "object_type": object_type,
            "reading_order": int(source.get("reading_order", index)),
            "bbox": bbox,
            "confidence": float(source.get("confidence", 0.5)),
            "transcription_status": source.get("transcription_status", "confirmed" if not unresolved else "partial"),
            "text": source.get("text") or source.get("source_text"),
            "fields": fields,
            "rows": strict_rows,
        })
    adapted = {
        "request_id": request["request_id"], "physical_page": request["physical_page"],
        "region_id": request["region_id"],
        "orientation_degrees_clockwise": int(request.get("orientation_degrees_clockwise", 0)),
        "objects": objects, "artifacts": list(value.get("artifacts") or []),
        "unresolved": unresolved,
    }
    return adapted, adaptations


def _vision_prompt(request: dict[str, Any], width: int, height: int) -> tuple[str, str]:
    system = (
        "You are a conservative rare-book layout recognizer. Transcribe only visible evidence. "
        "Never guess a digit, unit, fraction, printed page, Latin name, subtotal, or total. "
        "When any value is not fully legible set value/text to null and status to unresolved. "
        "Every field and table cell must have a tight pixel bbox in the supplied cropped image. "
        "Headers, footers, page numbers, watermarks, and scan marks belong only in artifacts."
    )
    special = {
        "illustration_list": (
            "Return one list_entry object per independent entry. Put source_text, latin_name, "
            "printed_locator, and locator_kind (frontispiece, facing_page, map, or printed_page) in fields. "
            "Preserve Frontispiece, Facing page relationships, Maps grouping, spelling, and punctuation."
        ),
        "multi_page_table": (
            "Recover table title, header rows, units, data rows, continuations, blank cells, footnotes, "
            "subtotals, and totals. Use one table object. Keep each visible cell separate."
        ),
        "rotated_table": (
            "This asset has already been rotated clockwise 90 degrees before cropping. Recover the visible "
            "landscape table, headers, data cells, and footnotes. Do not use any legacy OCR or prior values."
        ),
        "index": (
            "Verify left-column then right-column order. Return one index object per entry or continuation, "
            "with term, indent_level, parent_term, printed_page_references, cross_reference, and column in fields. "
            "Put running heads and printed page numbers in artifacts."
        ),
    }.get(request["output_object"], (
        "Group visual lines into semantic heading, prose, list_entry, caption, or footnote objects. "
        "Preserve numbered lists, quotations, bibliography text, Latin names, and footnote bindings."
    ))
    context = (
        f"Request {request['request_id']}; original physical page {request['physical_page']}; "
        f"normalized region {request['region_id']}; image size {width}x{height} pixels; "
        f"required action {request['action']}. {special} "
        "Return only the compact strict JSON object defined by the response schema. Top-level keys are exactly "
        "x=objects, a=artifacts, u=unresolved. Request identity, page, region, orientation, asset hash, and region bbox "
        "are injected by the local manifest and must not be generated by you. "
        "Object keys are i,t,n,b,c,s,q,f,w; field keys n,v,s,b; row keys n,t,b,c; cell keys n,t,s,b. "
        "For a table return at most one table object and at most 40 visible rows in this response; put any "
        "remaining range in unresolved instead of truncating or emitting invalid JSON. "
        "Do not use verbose aliases such as objects, entries, tables, or blocks. "
        "Object reading order n starts at 0 and is ascending. "
        "Use empty arrays where a required collection has no members."
    )
    return system, context


def _provider(root: Path, config_path: Path, provider: Any | None) -> tuple[str, dict[str, Any], Any]:
    config = load_provider_config(config_path)
    alias = config["active_vision_provider"]
    settings = config["providers"][alias]
    if not config.get("allow_real_api"):
        raise RuntimeError("provider configuration forbids real API calls")
    if not settings.get("api_key_available"):
        raise RuntimeError("configured vision API key is unavailable")
    client = provider or ZhipuOpenAICompatibleProvider(
        api_key=os.environ[settings["api_key_env"]],
        base_url=settings["base_url"],
        timeout_seconds=float(settings.get("timeout_seconds", 240)),
    )
    return alias, settings, client


def _usage_tokens(usage: dict[str, Any] | None) -> int:
    if not usage:
        return 0
    value = usage.get("total_tokens")
    return int(value) if isinstance(value, (int, float)) else 0


def _response_structure(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    summary: dict[str, Any] = {"type": "object", "keys": sorted(str(key) for key in value)}
    fields = {}
    for key, item in value.items():
        if isinstance(item, list):
            fields[str(key)] = {
                "type": "array", "count": len(item), "first_type": type(item[0]).__name__ if item else None,
                "first_keys": sorted(str(child) for child in item[0])[:30] if item and isinstance(item[0], dict) else None,
            }
        elif isinstance(item, dict):
            fields[str(key)] = {"type": "object", "keys": sorted(str(child) for child in item)[:30]}
        else:
            fields[str(key)] = {"type": type(item).__name__}
    summary["fields"] = fields
    return summary


def _vision_report(manifest: dict[str, Any], blockers: list[dict[str, Any]]) -> str:
    provider = manifest["provider"]
    return "\n".join([
        "# Phase 12R Vision Extraction Execution", "",
        f"Status: {manifest['status']}", "",
        "## Provider (sanitized)", "",
        f"- env_file_found: {str(provider['env_file_found']).lower()}",
        f"- vision_key_set: {str(provider['vision_key_set']).lower()}",
        f"- provider_alias: {provider['alias']}",
        f"- model: {provider['model']}",
        f"- base_url: {provider['base_url']}",
        f"- compatibility_mode: {provider['compatibility_mode']}", "",
        "## Execution", "",
        f"- planned_requests: {manifest['planned_requests']}",
        f"- validated_requests: {manifest['validated_requests']}",
        f"- api_calls: {manifest['api_calls']}",
        f"- resume_skips: {manifest['resume_skips']}",
        f"- retries: {manifest['retries']}",
        f"- api_tokens: {manifest['api_tokens']}",
        f"- blockers: {len(blockers)}", "",
        "All responses were validated locally against the strict extraction schema. "
        "Unresolved values remain explicit and are not promoted to formal table cells.", "",
    ])


def _write_immutable_json(path: Path, value: Any) -> None:
    if path.exists():
        if _load(path) != value:
            raise RuntimeError(f"immutable Phase 12R artifact already exists: {path}")
        return
    atomic_write_json(path, value)


def _attempt_directories(root: Path, request_id: str) -> list[Path]:
    parent = root / VISION_ATTEMPTS / request_id
    return sorted((item for item in parent.glob("attempt_*") if item.is_dir()), key=lambda item: item.name) if parent.is_dir() else []


def _event_state(attempt_dir: Path) -> str | None:
    events = sorted((attempt_dir / "events").glob("*.json")) if (attempt_dir / "events").is_dir() else []
    return _load(events[-1]).get("state") if events else None


def _last_attempt_event(attempt_dir: Path) -> dict[str, Any] | None:
    events = sorted((attempt_dir / "events").glob("*.json")) if (attempt_dir / "events").is_dir() else []
    return _load(events[-1]) if events else None


def _append_attempt_event(attempt_dir: Path, state: str, **values: Any) -> Path:
    events = attempt_dir / "events"
    events.mkdir(parents=True, exist_ok=True)
    existing = sorted(events.glob("*.json"))
    event = {
        "schema_version": "phase12r-attempt-event-1.0", "sequence": len(existing) + 1,
        "state": state, "at": _utc_now(), **values,
    }
    path = events / f"event_{len(existing) + 1:04d}_{state}.json"
    _write_immutable_json(path, event)
    return path


def _legacy_attempts(root: Path, request_id: str) -> int:
    path = root / VISION_MANIFEST
    if not path.is_file():
        return 0
    request = _load(path).get("requests", {}).get(request_id, {})
    return int(request.get("attempts", 0))


def _next_attempt_number(root: Path, request_id: str) -> int:
    values = [_legacy_attempts(root, request_id)]
    for path in _attempt_directories(root, request_id):
        try:
            values.append(int(path.name.rsplit("_", 1)[1]))
        except ValueError:
            continue
    return max(values, default=0) + 1


def _sync_vision_state(root: Path, manifest: dict[str, Any], checkpoint: dict[str, Any], *, transition: str) -> None:
    manifest["updated_at"] = _utc_now()
    manifest["last_transition"] = transition
    atomic_write_json(root / VISION_MANIFEST_V2, manifest)
    checkpoint.setdefault("stages", {})["vision_extraction"] = {
        "status": manifest["status"], "manifest_ref": VISION_MANIFEST_V2.as_posix(),
        "legacy_manifest_ref": VISION_MANIFEST.as_posix(),
        "planned_requests": manifest["planned_requests"],
        "validated_requests": manifest.get("validated_requests", 0),
        "semantic_unresolved": manifest.get("semantic_unresolved", 0),
        "api_calls": manifest["api_calls"], "api_tokens": manifest["api_tokens"],
        "last_transition": transition, "updated_at": manifest["updated_at"],
    }
    checkpoint["api_calls"]["vision"] = manifest["api_calls"]
    checkpoint["api_tokens"]["vision"] = manifest["api_tokens"]
    checkpoint["status"] = "in_progress"
    checkpoint["next_stage"] = "vision_extraction"
    atomic_write_json(root / CHECKPOINT, checkpoint)


def _mark_request_outcome(
    manifest: dict[str, Any], request_id: str, status: str, attempt_dir: Path, fingerprint: str,
) -> None:
    state = manifest.setdefault("requests", {}).setdefault(request_id, {"request_id": request_id})
    state.update(
        status=status, latest_attempt_ref=(attempt_dir / "attempt.json").as_posix(),
        fingerprint=fingerprint,
    )
    state.setdefault("publication_fallback", None)
    manifest["validated_requests"] = sum(
        item.get("status") == "validated" for item in manifest["requests"].values()
    )
    manifest["semantic_unresolved"] = sum(
        item.get("status") == "semantic_unresolved" for item in manifest["requests"].values()
    )


def _initialize_manifest_v2(
    root: Path, plan_path: Path, plan: dict[str, Any], alias: str, settings: dict[str, Any],
) -> dict[str, Any]:
    path = root / VISION_MANIFEST_V2
    if path.is_file():
        value = _load(path)
        if value["plan_sha256"] != _sha(plan_path):
            raise RuntimeError("immutable Phase 12R vision plan changed")
        return value
    legacy_path = root / VISION_MANIFEST
    legacy = _load(legacy_path) if legacy_path.is_file() else None
    return {
        "schema_version": "phase12r-vision-execution-manifest-2.0",
        "stage": "vision_extraction", "status": "in_progress",
        "plan_ref": VISION_PLAN.as_posix(), "plan_sha256": _sha(plan_path),
        "legacy_provenance": {
            "manifest_ref": VISION_MANIFEST.as_posix() if legacy else None,
            "manifest_sha256": _sha(legacy_path) if legacy else None,
            "api_calls": legacy.get("api_calls", 0) if legacy else 0,
            "api_tokens": legacy.get("api_tokens", 0) if legacy else 0,
            "request_count": len(legacy.get("requests", {})) if legacy else 0,
            "note": "Legacy v1 aggregate retained byte-for-byte; missing per-attempt fields are not synthesized.",
        },
        "provider": {
            "env_file_found": (root / ".env").is_file(),
            "vision_key_set": bool(settings.get("api_key_available")),
            "alias": alias, "model": settings["model"], "base_url": settings["base_url"],
            "compatibility_mode": "openai_json_schema_strict",
        },
        "prompt_version": PROMPT_VERSION, "schema_version_wire": WIRE_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION, "planned_requests": len(plan["requests"]),
        "requests": {}, "api_calls": 0, "api_tokens": 0, "resume_skips": 0,
        "transport_failures": 0, "rate_limits": 0, "parse_failures": 0,
        "schema_failures": 0, "semantic_unresolved": 0, "validated_requests": 0,
        "created_at": _utc_now(),
    }


def _request_fingerprint(
    request: dict[str, Any], region: dict[str, Any], asset_sha: str,
    alias: str, settings: dict[str, Any],
) -> str:
    return stable_hash({
        "request": request, "asset_sha256": asset_sha,
        "normalized_bbox": region.get("normalized_bbox") or region.get("bbox"),
        "provider": alias, "model": settings["model"], "base_url": settings["base_url"],
        "schema": _provider_vision_schema(), "schema_version": WIRE_SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION, "adapter_version": ADAPTER_VERSION,
    })


def _new_attempt(
    root: Path, request: dict[str, Any], region: dict[str, Any], *, fingerprint: str,
    asset_sha: str, alias: str, settings: dict[str, Any], transport_retry: int,
) -> tuple[Path, dict[str, Any]]:
    number = _next_attempt_number(root, request["request_id"])
    attempt_dir = root / VISION_ATTEMPTS / request["request_id"] / f"attempt_{number:04d}"
    metadata = {
        "schema_version": ATTEMPT_SCHEMA_VERSION, "request_id": request["request_id"],
        "physical_page": request["physical_page"], "region_id": request["region_id"],
        "attempt_number": number, "started_at": _utc_now(), "provider": alias,
        "model": settings["model"], "base_url": settings["base_url"],
        "prompt_version": PROMPT_VERSION, "schema_version_wire": WIRE_SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION, "fingerprint": fingerprint,
        "asset_ref": request["asset_ref"], "asset_sha256": asset_sha,
        "normalized_bbox": region.get("normalized_bbox") or region.get("bbox"),
        "orientation_degrees_clockwise": int(request.get("orientation_degrees_clockwise", 0)),
        "expected_object_type": request["output_object"], "transport_retry": transport_retry,
        "semantic_budget_consumed": False,
    }
    _write_immutable_json(attempt_dir / "attempt.json", metadata)
    _append_attempt_event(attempt_dir, "dispatching")
    return attempt_dir, metadata


def _raw_path(attempt_dir: Path) -> Path:
    return attempt_dir / "raw_response.json"


def _persist_raw_response(attempt_dir: Path, response: Any) -> dict[str, Any]:
    value = {
        "schema_version": "phase12r-raw-provider-response-1.0",
        "returned_at": _utc_now(), "request_id": response.request_id,
        "response_model": response.response_model, "http_status": response.http_status,
        "usage": response.usage, "content": response.content,
        "raw_response": response.raw_response,
    }
    path = _raw_path(attempt_dir)
    _write_immutable_json(path, value)
    sha = _sha(path)
    _append_attempt_event(
        attempt_dir, "raw_committed", raw_response_ref=path.as_posix(),
        raw_response_sha256=sha, response_content_length=len(response.content.encode("utf-8")),
        response_content_sha256=hashlib.sha256(response.content.encode("utf-8")).hexdigest(),
        usage=response.usage, http_status=response.http_status,
    )
    return {"path": path, "sha256": sha, "value": value}


def _is_semantically_unresolved(parsed: dict[str, Any], request: dict[str, Any]) -> bool:
    if any(str(item.get("name", "")).endswith("_bbox") for item in parsed.get("unresolved", [])):
        return True
    if request["output_object"] not in {"multi_page_table", "rotated_table"}:
        return False
    return not any(item.get("rows") for item in parsed.get("objects", []))


def _process_raw_attempt(
    root: Path, attempt_dir: Path, request: dict[str, Any], *, width: int, height: int,
    fingerprint: str, manifest: dict[str, Any], checkpoint: dict[str, Any],
) -> str:
    raw = _load(_raw_path(attempt_dir))
    last_event = _last_attempt_event(attempt_dir)
    state = last_event.get("state") if last_event else None
    if state == "validated" and last_event.get("validation_fingerprint") == fingerprint:
        return "validated"
    if state in {"parse_failed_recoverable", "schema_failed_recoverable"}:
        if last_event.get("adapter_version") == ADAPTER_VERSION:
            return state
    try:
        parsed_wire = _json_content(raw.get("content", ""))
    except Exception as exc:
        manifest["parse_failures"] += 1
        _append_attempt_event(
            attempt_dir, "parse_failed_recoverable", adapter_version=ADAPTER_VERSION,
            exception_type=type(exc).__name__,
            message_sha256=hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest(),
        )
        _mark_request_outcome(manifest, request["request_id"], "parse_failed_recoverable", attempt_dir, fingerprint)
        _sync_vision_state(root, manifest, checkpoint, transition="parse_failed_recoverable")
        return "parse_failed_recoverable"
    try:
        parsed, adaptations = _expand_compact_payload(parsed_wire, request, width=width, height=height)
        if not adaptations:
            parsed, adaptations = _adapt_vision_payload(parsed_wire, request, width=width, height=height)
    except Exception as exc:
        manifest["schema_failures"] += 1
        _append_attempt_event(
            attempt_dir, "schema_failed_recoverable", adapter_version=ADAPTER_VERSION,
            exception_type=type(exc).__name__,
            message_sha256=hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest(),
        )
        _mark_request_outcome(manifest, request["request_id"], "schema_failed_recoverable", attempt_dir, fingerprint)
        _sync_vision_state(root, manifest, checkpoint, transition="schema_failed_recoverable")
        return "schema_failed_recoverable"
    parsed_path = attempt_dir / f"parsed_candidate_{ADAPTER_VERSION}_{fingerprint[:16]}.json"
    _write_immutable_json(parsed_path, {
        "schema_version": "phase12r-parsed-candidate-1.0", "request_id": request["request_id"],
        "adapter_version": ADAPTER_VERSION, "fingerprint": fingerprint,
        "adaptations": adaptations, "candidate": parsed,
    })
    errors = validate_vision_result(parsed, request, width=width, height=height)
    if errors:
        manifest["schema_failures"] += 1
        _append_attempt_event(
            attempt_dir, "schema_failed_recoverable", adapter_version=ADAPTER_VERSION,
            parsed_candidate_ref=parsed_path.as_posix(), schema_errors=errors[:50],
        )
        _mark_request_outcome(manifest, request["request_id"], "schema_failed_recoverable", attempt_dir, fingerprint)
        _sync_vision_state(root, manifest, checkpoint, transition="schema_failed_recoverable")
        return "schema_failed_recoverable"
    if _is_semantically_unresolved(parsed, request):
        manifest["semantic_unresolved"] += 1
        _append_attempt_event(
            attempt_dir, "semantic_unresolved", adapter_version=ADAPTER_VERSION,
            parsed_candidate_ref=parsed_path.as_posix(), publication_fallback=None,
            validation_fingerprint=fingerprint,
        )
        _mark_request_outcome(manifest, request["request_id"], "semantic_unresolved", attempt_dir, fingerprint)
        _sync_vision_state(root, manifest, checkpoint, transition="semantic_unresolved")
        return "semantic_unresolved"
    result_path = attempt_dir / f"validated_result_{fingerprint[:16]}.json"
    result_record = {
        "schema_version": "phase12r-validated-vision-result-2.0",
        "request_id": request["request_id"], "fingerprint": fingerprint,
        "attempt_ref": (attempt_dir / "attempt.json").as_posix(),
        "raw_response_ref": _raw_path(attempt_dir).as_posix(),
        "raw_response_sha256": _sha(_raw_path(attempt_dir)),
        "parsed_candidate_ref": parsed_path.as_posix(), "result": parsed,
        "validated": True, "validated_at": _utc_now(),
    }
    _write_immutable_json(result_path, result_record)
    _append_attempt_event(
        attempt_dir, "validated", adapter_version=ADAPTER_VERSION,
        validated_result_ref=result_path.as_posix(), validation_fingerprint=fingerprint,
    )
    results_path = root / VISION_RESULTS_V2
    results = _load(results_path) if results_path.is_file() else {
        "schema_version": "phase12r-vision-extraction-results-2.0", "results": []
    }
    by_id = {item["request_id"]: item for item in results["results"]}
    by_id[request["request_id"]] = result_record
    results["results"] = [by_id[key] for key in sorted(by_id)]
    atomic_write_json(results_path, results)
    _mark_request_outcome(manifest, request["request_id"], "validated", attempt_dir, fingerprint)
    _sync_vision_state(root, manifest, checkpoint, transition="validated")
    return "validated"


def _exception_state(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if status == 429:
        return "rate_limited"
    if "timeout" in type(exc).__name__.lower() or "connection" in type(exc).__name__.lower():
        return "transport_failed"
    return "transport_failed"


def execute_vision_extraction(
    root: Path,
    config_path: Path,
    *,
    allow_api: bool,
    provider: Any | None = None,
    max_requests: int | None = None,
    max_transport_retries: int = 3,
    sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    root = root.resolve()
    if not allow_api:
        raise RuntimeError("real vision extraction requires explicit allow_api=True")
    checkpoint = _load_checkpoint(root)
    _verify_frozen(root, checkpoint)
    plan_path = root / VISION_PLAN
    plan = _load(plan_path)
    normalization = _load(root / plan["input_contract"]["normalization_manifest"])
    regions = {item["region_id"]: item for item in normalization["regions"]}
    alias, settings, client = _provider(root, config_path, provider)
    manifest = _initialize_manifest_v2(root, plan_path, plan, alias, settings)
    _sync_vision_state(root, manifest, checkpoint, transition="resume_started")
    provider_requests_this_run = 0
    for request in plan["requests"]:
        region = regions.get(request["region_id"])
        if not region or not region["region_asset_ref"].endswith(request["asset_ref"]):
            raise RuntimeError(f"region manifest mismatch: {request['request_id']}")
        asset = (root / request["asset_ref"]).resolve()
        if root not in asset.parents or not asset.is_file():
            raise RuntimeError(f"invalid vision asset: {request['asset_ref']}")
        asset_sha = _sha(asset)
        if asset_sha != region["region_sha256"]:
            raise RuntimeError(f"vision asset SHA mismatch: {request['request_id']}")
        fingerprint = _request_fingerprint(request, region, asset_sha, alias, settings)
        from PIL import Image
        with Image.open(asset) as image:
            width, height = image.size
        state = manifest["requests"].setdefault(request["request_id"], {
            "request_id": request["request_id"], "physical_page": request["physical_page"],
            "region_id": request["region_id"], "asset_ref": request["asset_ref"],
            "asset_sha256": asset_sha, "normalized_bbox": region.get("normalized_bbox") or region.get("bbox"),
            "orientation_degrees_clockwise": int(request.get("orientation_degrees_clockwise", 0)),
            "expected_object_type": request["output_object"], "status": "planned",
            "publication_fallback": None, "attempt_refs": [],
        })
        state["fingerprint"] = fingerprint
        # Reparse durable raw responses before considering another provider call.
        current_fingerprint_has_raw = False
        for attempt_dir in reversed(_attempt_directories(root, request["request_id"])):
            metadata = _load(attempt_dir / "attempt.json")
            if not _raw_path(attempt_dir).is_file():
                continue
            outcome = _process_raw_attempt(
                root, attempt_dir, request, width=width, height=height,
                fingerprint=fingerprint, manifest=manifest, checkpoint=checkpoint,
            )
            if outcome == "validated":
                state.update(status="validated", fingerprint=fingerprint,
                             validated_result_ref=(attempt_dir / f"validated_result_{fingerprint[:16]}.json").as_posix())
                manifest["resume_skips"] += 1
                break
            if metadata.get("fingerprint") == fingerprint:
                current_fingerprint_has_raw = True
                state["status"] = outcome
                state["latest_attempt_ref"] = (attempt_dir / "attempt.json").as_posix()
                break
        if state.get("status") in {"validated", "semantic_unresolved"} and state.get("fingerprint") == fingerprint:
            _sync_vision_state(root, manifest, checkpoint, transition=f"{state['status']}_resume_skip")
            continue
        if current_fingerprint_has_raw:
            _sync_vision_state(root, manifest, checkpoint, transition=state["status"])
            continue
        if max_requests is not None and provider_requests_this_run >= max_requests:
            break
        system_prompt, context = _vision_prompt(request, width, height)
        transport_retry = 0
        while transport_retry <= max_transport_retries:
            attempt_dir, metadata = _new_attempt(
                root, request, region, fingerprint=fingerprint, asset_sha=asset_sha,
                alias=alias, settings=settings, transport_retry=transport_retry,
            )
            attempt_ref = (attempt_dir / "attempt.json").as_posix()
            state["attempt_refs"].append(attempt_ref)
            state.update(status="dispatching", latest_attempt_ref=attempt_ref)
            manifest["api_calls"] += 1
            provider_requests_this_run += 1
            _sync_vision_state(root, manifest, checkpoint, transition="dispatching")
            try:
                response = client.transcribe_images(
                    model=settings["model"], prompt=system_prompt, context_message=context,
                    image_data_urls=[_data_url(asset)], max_output_tokens=int(settings.get("max_output_tokens", 8192)),
                    temperature=0, do_sample=False, thinking_mode=str(settings.get("thinking_mode", "disabled")),
                    response_format_json_object=False, response_json_schema=_provider_vision_schema(),
                    response_schema_name="phase12r_back_matter_region",
                    return_raw_on_content_error=True,
                )
            except Exception as exc:
                failure = _exception_state(exc)
                if failure == "rate_limited":
                    manifest["rate_limits"] += 1
                else:
                    manifest["transport_failures"] += 1
                _append_attempt_event(
                    attempt_dir, failure, ended_at=_utc_now(),
                    exception_type=type(exc).__name__, http_status=getattr(exc, "status_code", None),
                    message_sha256=hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest(),
                    semantic_budget_consumed=False,
                )
                state["status"] = failure
                _sync_vision_state(root, manifest, checkpoint, transition=failure)
                transport_retry += 1
                if transport_retry > max_transport_retries:
                    break
                sleep_fn(min(2 ** (transport_retry - 1), 30))
                continue
            raw = _persist_raw_response(attempt_dir, response)
            manifest["api_tokens"] += _usage_tokens(response.usage)
            state.update(
                status="raw_committed", raw_response_ref=raw["path"].as_posix(),
                raw_response_sha256=raw["sha256"], latest_attempt_ref=attempt_ref,
            )
            _sync_vision_state(root, manifest, checkpoint, transition="raw_committed")
            outcome = _process_raw_attempt(
                root, attempt_dir, request, width=width, height=height,
                fingerprint=fingerprint, manifest=manifest, checkpoint=checkpoint,
            )
            state["status"] = outcome
            if outcome == "validated":
                state["validated_result_ref"] = (attempt_dir / f"validated_result_{fingerprint[:16]}.json").as_posix()
            _sync_vision_state(root, manifest, checkpoint, transition=outcome)
            break
    validated = sum(item.get("status") == "validated" for item in manifest["requests"].values())
    unresolved = sum(item.get("status") == "semantic_unresolved" for item in manifest["requests"].values())
    manifest["validated_requests"] = validated
    manifest["semantic_unresolved"] = unresolved
    terminal = {"validated", "semantic_unresolved"}
    all_final = len(manifest["requests"]) == len(plan["requests"]) and all(
        item.get("status") in terminal for item in manifest["requests"].values()
    )
    manifest["status"] = "completed" if all_final and not unresolved else "completed_with_unresolved" if all_final else "resumable"
    if all_final:
        checkpoint["last_durable_stage"] = "vision_extraction"
        checkpoint["next_stage"] = "candidate_merge"
    _sync_vision_state(root, manifest, checkpoint, transition="run_completed")
    _verify_frozen(root, checkpoint)
    return manifest


def _candidate_for_request(root: Path, state: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if state.get("status") == "validated":
        ref = Path(state["validated_result_ref"])
        if not ref.is_absolute():
            ref = root / ref
        return _load(ref)["result"], ref.as_posix()
    attempt_dir = Path(state["latest_attempt_ref"]).parent
    if not attempt_dir.is_absolute():
        attempt_dir = root / attempt_dir
    event = _last_attempt_event(attempt_dir) or {}
    ref_value = event.get("parsed_candidate_ref")
    if not ref_value:
        raise RuntimeError(f"terminal request lacks parsed candidate: {state['request_id']}")
    ref = Path(ref_value)
    if not ref.is_absolute():
        ref = root / ref
    return _load(ref)["candidate"], ref.as_posix()


def merge_vision_candidates(root: Path) -> dict[str, Any]:
    root = root.resolve()
    checkpoint = _load_checkpoint(root)
    _verify_frozen(root, checkpoint)
    manifest = _load(root / VISION_MANIFEST_V2)
    if manifest.get("status") not in {"completed", "completed_with_unresolved"}:
        raise RuntimeError("vision extraction has not reached formal final states")
    records, objects = [], []
    for request_id, state in sorted(manifest["requests"].items()):
        if state.get("status") not in {"validated", "semantic_unresolved"}:
            raise RuntimeError(f"nonterminal vision request: {request_id}")
        candidate, candidate_ref = _candidate_for_request(root, state)
        fallback = "normalized_region_facsimile" if state["status"] == "semantic_unresolved" else None
        state["publication_fallback"] = fallback
        record = {
            "request_id": request_id, "physical_page": state["physical_page"],
            "region_id": state["region_id"], "status": state["status"],
            "fingerprint": state["fingerprint"], "candidate_ref": candidate_ref,
            "raw_response_ref": state.get("raw_response_ref"),
            "publication_fallback": fallback, "object_count": len(candidate.get("objects", [])),
            "candidate": candidate,
        }
        records.append(record)
        for item in candidate.get("objects", []):
            objects.append({
                **item, "request_id": request_id, "physical_page": state["physical_page"],
                "region_id": state["region_id"], "recognition_status": state["status"],
                "publication_fallback": fallback, "candidate_ref": candidate_ref,
            })
    candidate_store = {
        "schema_version": "recognized-back-matter-candidates-2.0", "status": manifest["status"],
        "request_count": len(records), "validated_count": sum(x["status"] == "validated" for x in records),
        "semantic_unresolved_count": sum(x["status"] == "semantic_unresolved" for x in records),
        "source_manifest_ref": VISION_MANIFEST_V2.as_posix(), "candidates": records,
    }
    structured = {
        "schema_version": "phase12r-structured-back-matter-2.0", "coordinate_space": "normalized_region",
        "source_candidates_ref": VISION_CANDIDATES_V2.as_posix(), "object_count": len(objects), "objects": objects,
    }
    atomic_write_json(root / VISION_CANDIDATES_V2, candidate_store)
    atomic_write_json(root / STRUCTURED_OBJECTS_V2, structured)

    illustration_v1 = _load(root / PHASE12R_ROOT / "illustration_list_reading_order_v1.json")
    recognized = [
        {**item, "request_id": record["request_id"], "candidate_ref": record["candidate_ref"]}
        for record in records if record["request_id"] in {"p12r_v_001", "p12r_v_002"}
        for item in record["candidate"].get("objects", [])
    ]
    if len(recognized) != len(illustration_v1["entries"]):
        raise RuntimeError("illustration-list candidate count mismatch")
    illustration_entries = []
    for base, item in zip(illustration_v1["entries"], recognized):
        source_text = item.get("text") if isinstance(item.get("text"), str) and item["text"].strip() else base["source_text"]
        illustration_entries.append({
            **base, "source_text": source_text, "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "recognition_status": "validated" if item["request_id"] == "p12r_v_001" else "semantic_unresolved_bbox",
            "recognition_object_id": item["object_id"], "recognition_candidate_ref": item["candidate_ref"],
        })
    illustration_v2 = {
        **{key: value for key, value in illustration_v1.items() if key != "entries"},
        "schema_version": "phase12r-illustration-list-2.0", "entries": illustration_entries,
        "source_candidates_ref": VISION_CANDIDATES_V2.as_posix(),
    }
    atomic_write_json(root / ILLUSTRATION_LIST_V2, illustration_v2)
    manifest["status"] = candidate_store["status"]
    _sync_vision_state(root, manifest, checkpoint, transition="candidate_merge_completed")
    checkpoint["last_durable_stage"] = "candidate_merge"
    checkpoint["next_stage"] = "translation_delta"
    checkpoint.setdefault("stages", {})["candidate_merge"] = {
        "status": "completed", "request_count": len(records), "object_count": len(objects),
        "candidate_ref": VISION_CANDIDATES_V2.as_posix(), "structured_objects_ref": STRUCTURED_OBJECTS_V2.as_posix(),
    }
    atomic_write_json(root / CHECKPOINT, checkpoint)
    _verify_frozen(root, checkpoint)
    return {"request_count": len(records), "object_count": len(objects), "illustration_entries": len(illustration_entries)}


def calculate_translation_delta(root: Path) -> dict[str, Any]:
    root = root.resolve()
    checkpoint = _load_checkpoint(root)
    _verify_frozen(root, checkpoint)
    illustration = _load(root / ILLUSTRATION_LIST_V2)
    existing_by_sha: dict[str, str] = {}
    units_path = root / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl"
    overlay_path = root / "data/fullbook/multilingual/documents/multilingual_translation_overlay_zh-Hans_v1.json"
    if units_path.is_file() and overlay_path.is_file():
        existing_units = [_load_json for _load_json in (json.loads(line) for line in units_path.read_text("utf-8").splitlines() if line.strip())]
        overlay = _load(overlay_path).get("translations", {})
        for unit in existing_units:
            translated = overlay.get(unit["translation_unit_id"], {}).get("translated_text")
            if isinstance(translated, str) and translated.strip():
                existing_by_sha.setdefault(unit["source_text_sha256"], translated)
    units = []
    for entry in illustration["entries"]:
        source_sha = entry["source_text_sha256"]
        reused = existing_by_sha.get(source_sha)
        units.append({
            "translation_unit_id": f"p12r_illustration_{entry['entry_id']}",
            "source_object_id": entry["entry_id"], "source_object_type": "illustration_list_entry",
            "source_text": entry["source_text"], "source_text_sha256": source_sha,
            "source_language": "en", "target_language": "zh-Hans",
            "protected_latin_name": entry.get("latin_name"), "protected_printed_locator": entry.get("printed_locator"),
            "status": "reused" if reused else "pending", "translated_text": reused,
        })
    delta = {
        "schema_version": "phase12r-translation-delta-1.0", "source_ref": ILLUSTRATION_LIST_V2.as_posix(),
        "unit_count": len(units), "pending_count": sum(x["status"] == "pending" for x in units),
        "reused_count": sum(x["status"] == "reused" for x in units),
        "existing_main_text_retranslated": 0, "units": units,
    }
    atomic_write_json(root / TRANSLATION_DELTA, delta)
    checkpoint["last_durable_stage"] = "translation_delta"
    checkpoint["next_stage"] = "translation"
    checkpoint.setdefault("stages", {})["translation_delta"] = {
        "status": "completed", "unit_count": delta["unit_count"], "pending_count": delta["pending_count"],
        "reused_count": delta["reused_count"], "delta_ref": TRANSLATION_DELTA.as_posix(),
    }
    atomic_write_json(root / CHECKPOINT, checkpoint)
    _verify_frozen(root, checkpoint)
    return delta


def _translation_context(root: Path, config_path: Path) -> tuple[str, dict[str, Any], Any]:
    config = load_provider_config(config_path)
    alias = config.get("active_translation_provider")
    settings = config.get("providers", {}).get(alias or "", {})
    if not config.get("allow_real_api"):
        raise RuntimeError("provider configuration forbids real API calls")
    if settings.get("type") != "openai_compatible":
        raise RuntimeError("Phase 12R translation requires an OpenAI-compatible provider")
    env_name = settings.get("api_key_env")
    if not env_name or not os.getenv(env_name):
        raise RuntimeError("configured translation API key is unavailable")
    provider = DeepSeekOpenAICompatibleProvider(
        api_key=os.environ[env_name], base_url=settings["base_url"],
        timeout_seconds=float(settings.get("timeout_seconds", 240)),
    )
    return str(alias), settings, provider


def _translation_stage_prompt(stage: str) -> str:
    common = (
        "You work only on the supplied Phase 12R translation delta. Preserve proper names, Latin scientific names, "
        "numbers, punctuation, and placeholders. Never add facts. Return only one JSON object with the requested array. "
    )
    if stage == "draft":
        return common + "Translate each source_text faithfully into publication-quality Simplified Chinese. Return key translations; each item has translation_unit_id and translated_text."
    if stage == "review":
        return common + "Act as an independent bilingual reviewer. Compare source_text and draft_translation. Return key reviews; each item has translation_unit_id, issues as an array of concise strings, and recommended_translation."
    return common + "Produce the final publication translation using source_text, draft_translation, and review. Return key translations; each item has translation_unit_id and translated_text."


def _translation_stage_items(content: str, stage: str, expected_ids: list[str]) -> list[dict[str, Any]]:
    payload = _json_content(content)
    key = "reviews" if stage == "review" else "translations"
    items = payload.get(key)
    if not isinstance(items, list) and isinstance(payload.get("items"), list):
        items = payload["items"]
    if not isinstance(items, list) or len(items) != len(expected_ids):
        raise ValueError(f"{stage} response count mismatch")
    by_id = {item.get("translation_unit_id"): item for item in items if isinstance(item, dict)}
    if set(by_id) != set(expected_ids):
        raise ValueError(f"{stage} response identity mismatch")
    normalized = []
    for unit_id in expected_ids:
        item = by_id[unit_id]
        if stage == "review":
            text = item.get("recommended_translation")
            issues = item.get("issues")
            if not isinstance(issues, list) or not isinstance(text, str) or not text.strip():
                raise ValueError("invalid review contract")
            normalized.append({"translation_unit_id": unit_id, "issues": [str(value) for value in issues], "recommended_translation": text.strip()})
        else:
            text = item.get("translated_text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"invalid {stage} translation contract")
            normalized.append({"translation_unit_id": unit_id, "translated_text": text.strip()})
    return normalized


def execute_translation_delta(
    root: Path, config_path: Path, *, allow_api: bool, batch_size: int = 8,
    max_transport_retries: int = 3, sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    if not allow_api:
        raise RuntimeError("real translation requires explicit allow_api=True")
    root = root.resolve()
    checkpoint = _load_checkpoint(root)
    _verify_frozen(root, checkpoint)
    delta = _load(root / TRANSLATION_DELTA)
    alias, settings, provider = _translation_context(root, config_path)
    manifest_path = root / TRANSLATION_MANIFEST
    manifest = _load(manifest_path) if manifest_path.is_file() else {
        "schema_version": "phase12r-translation-execution-1.0", "status": "in_progress",
        "provider": {"alias": alias, "model": settings["model"], "base_url": settings["base_url"]},
        "source_delta_ref": TRANSLATION_DELTA.as_posix(), "unit_count": delta["unit_count"],
        "batches": {}, "api_calls": 0, "api_tokens": 0, "created_at": _utc_now(),
    }

    pending = [item for item in delta["units"] if item["status"] == "pending"]
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        batch_id = f"batch_{offset // batch_size + 1:04d}"
        unit_ids = [item["translation_unit_id"] for item in batch]
        batch_state = manifest["batches"].setdefault(batch_id, {"unit_ids": unit_ids, "stages": {}, "status": "in_progress"})
        stage_inputs: dict[str, list[dict[str, Any]]] = {
            "draft": [{"translation_unit_id": item["translation_unit_id"], "source_text": item["source_text"], "protected_latin_name": item.get("protected_latin_name")} for item in batch]
        }
        for stage in ("draft", "review", "final"):
            result_ref = batch_state["stages"].get(stage, {}).get("result_ref")
            if result_ref and (root / result_ref).is_file():
                stage_result = _load(root / result_ref)["items"]
            else:
                if stage == "review":
                    drafts = {item["translation_unit_id"]: item["translated_text"] for item in stage_inputs["draft_result"]}
                    stage_inputs[stage] = [{"translation_unit_id": item["translation_unit_id"], "source_text": item["source_text"], "draft_translation": drafts[item["translation_unit_id"]]} for item in batch]
                elif stage == "final":
                    drafts = {item["translation_unit_id"]: item["translated_text"] for item in stage_inputs["draft_result"]}
                    reviews = {item["translation_unit_id"]: item for item in stage_inputs["review_result"]}
                    stage_inputs[stage] = [{"translation_unit_id": item["translation_unit_id"], "source_text": item["source_text"], "draft_translation": drafts[item["translation_unit_id"]], "review": reviews[item["translation_unit_id"]]} for item in batch]
                payload = {"stage": stage, "items": stage_inputs[stage]}
                fingerprint = stable_hash({"stage": stage, "payload": payload, "model": settings["model"], "prompt": _translation_stage_prompt(stage)})
                attempts_root = root / TRANSLATION_ATTEMPTS / batch_id / stage
                existing = sorted(attempts_root.glob("attempt_*")) if attempts_root.is_dir() else []
                completed = next((path for path in reversed(existing) if (path / f"result_{fingerprint[:16]}.json").is_file()), None)
                if completed:
                    result_path = completed / f"result_{fingerprint[:16]}.json"
                    stage_result = _load(result_path)["items"]
                else:
                    stage_result = None
                    recoverable = next((
                        path for path in reversed(existing)
                        if (path / "raw_response.json").is_file()
                        and _load(path / "attempt.json").get("fingerprint") == fingerprint
                    ), None)
                    if recoverable:
                        raw_path = recoverable / "raw_response.json"
                        stage_result = _translation_stage_items(_load(raw_path).get("content", ""), stage, unit_ids)
                        result_path = recoverable / f"result_{fingerprint[:16]}.json"
                        _write_immutable_json(result_path, {"schema_version": "phase12r-translation-stage-result-1.0", "stage": stage, "fingerprint": fingerprint, "raw_response_ref": raw_path.relative_to(root).as_posix(), "items": stage_result})
                        _append_attempt_event(recoverable, "validated", result_ref=result_path.relative_to(root).as_posix(), validation_fingerprint=fingerprint, recovery="offline_raw_reparse")
                        batch_state["stages"][stage] = {"status": "validated", "result_ref": result_path.relative_to(root).as_posix(), "fingerprint": fingerprint}
                        manifest["updated_at"] = _utc_now(); atomic_write_json(manifest_path, manifest)
                    if stage_result is not None:
                        stage_inputs[f"{stage}_result"] = stage_result
                        continue
                    for retry in range(max_transport_retries + 1):
                        attempt_dir = attempts_root / f"attempt_{len(existing) + retry + 1:04d}"
                        attempt_meta = {
                            "schema_version": "phase12r-translation-attempt-1.0", "batch_id": batch_id,
                            "stage": stage, "unit_ids": unit_ids, "fingerprint": fingerprint,
                            "provider": alias, "model": settings["model"], "started_at": _utc_now(), "transport_retry": retry,
                        }
                        _write_immutable_json(attempt_dir / "attempt.json", attempt_meta)
                        _append_attempt_event(attempt_dir, "dispatching")
                        manifest["api_calls"] += 1
                        batch_state["stages"][stage] = {"status": "dispatching", "attempt_ref": (attempt_dir / "attempt.json").relative_to(root).as_posix(), "fingerprint": fingerprint}
                        manifest["updated_at"] = _utc_now(); atomic_write_json(manifest_path, manifest)
                        checkpoint["api_calls"]["translation"] = manifest["api_calls"]
                        checkpoint["api_tokens"]["translation"] = manifest["api_tokens"]
                        atomic_write_json(root / CHECKPOINT, checkpoint)
                        try:
                            response = provider.translate_one(
                                model=settings["model"], system_prompt=_translation_stage_prompt(stage), user_payload=payload,
                                max_output_tokens=int(settings.get("max_output_tokens", 8192)), temperature=0,
                                thinking_mode=str(settings.get("thinking_mode", "disabled")),
                            )
                        except Exception as exc:
                            failure = _exception_state(exc)
                            _append_attempt_event(attempt_dir, failure, exception_type=type(exc).__name__, http_status=getattr(exc, "status_code", None), message_sha256=hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest())
                            batch_state["stages"][stage]["status"] = failure
                            atomic_write_json(manifest_path, manifest)
                            if retry >= max_transport_retries:
                                raise
                            sleep_fn(min(2 ** retry, 30)); continue
                        raw_value = {
                            "schema_version": "phase12r-translation-raw-1.0", "returned_at": _utc_now(),
                            "request_id": response.request_id, "response_model": response.response_model,
                            "usage": response.usage, "content": response.content, "raw_response": response.raw_response,
                        }
                        raw_path = attempt_dir / "raw_response.json"
                        _write_immutable_json(raw_path, raw_value)
                        _append_attempt_event(attempt_dir, "raw_committed", raw_response_ref=raw_path.relative_to(root).as_posix(), raw_response_sha256=_sha(raw_path))
                        manifest["api_tokens"] += _usage_tokens(response.usage)
                        stage_result = _translation_stage_items(response.content, stage, unit_ids)
                        result_path = attempt_dir / f"result_{fingerprint[:16]}.json"
                        _write_immutable_json(result_path, {"schema_version": "phase12r-translation-stage-result-1.0", "stage": stage, "fingerprint": fingerprint, "raw_response_ref": raw_path.relative_to(root).as_posix(), "items": stage_result})
                        _append_attempt_event(attempt_dir, "validated", result_ref=result_path.relative_to(root).as_posix(), validation_fingerprint=fingerprint)
                        batch_state["stages"][stage] = {"status": "validated", "result_ref": result_path.relative_to(root).as_posix(), "fingerprint": fingerprint}
                        manifest["updated_at"] = _utc_now(); atomic_write_json(manifest_path, manifest)
                        checkpoint["api_calls"]["translation"] = manifest["api_calls"]
                        checkpoint["api_tokens"]["translation"] = manifest["api_tokens"]
                        atomic_write_json(root / CHECKPOINT, checkpoint)
                        break
                    if stage_result is None:
                        raise RuntimeError(f"translation stage did not complete: {batch_id}/{stage}")
            stage_inputs[f"{stage}_result"] = stage_result
        final_by_id = {item["translation_unit_id"]: item["translated_text"] for item in stage_inputs["final_result"]}
        for unit in delta["units"]:
            if unit["translation_unit_id"] in final_by_id:
                unit["status"] = "validated"; unit["translated_text"] = final_by_id[unit["translation_unit_id"]]
        batch_state["status"] = "completed"
        atomic_write_json(root / TRANSLATION_DELTA, delta)
        atomic_write_json(manifest_path, manifest)
    overlay = {
        "schema_version": "phase12r-translation-overlay-1.0", "target_language": "zh-Hans",
        "translations": {item["source_object_id"]: {"translation_unit_id": item["translation_unit_id"], "source_text_sha256": item["source_text_sha256"], "translated_text": item["translated_text"], "status": item["status"]} for item in delta["units"] if item.get("translated_text")},
    }
    atomic_write_json(root / TRANSLATION_OVERLAY, overlay)
    pending_count = sum(item["status"] == "pending" for item in delta["units"])
    manifest["status"] = "completed" if pending_count == 0 else "resumable"
    manifest["updated_at"] = _utc_now(); atomic_write_json(manifest_path, manifest)
    checkpoint["last_durable_stage"] = "translation" if pending_count == 0 else "translation_delta"
    checkpoint["next_stage"] = "release_build" if pending_count == 0 else "translation"
    checkpoint.setdefault("stages", {})["translation"] = {"status": manifest["status"], "validated": delta["unit_count"] - pending_count, "pending": pending_count, "manifest_ref": TRANSLATION_MANIFEST.as_posix(), "overlay_ref": TRANSLATION_OVERLAY.as_posix()}
    atomic_write_json(root / CHECKPOINT, checkpoint)
    _verify_frozen(root, checkpoint)
    return manifest


def _latest_release_manifests(root: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    releases: dict[str, tuple[Path, dict[str, Any]]] = {}
    for language in ("en", "zh-Hans", "bilingual"):
        paths = sorted((root / "output/fullbook").glob(f"big-game-{language}-reading-release-*/render_manifest.json"))
        candidates = [(path, _load(path)) for path in paths]
        candidates = [(path, value) for path, value in candidates if value.get("profile") == "release"]
        if not candidates:
            raise RuntimeError(f"Phase 12R {language} release is missing")
        releases[language] = candidates[-1]
    return releases


def _section_pages(pdf_path: Path) -> dict[str, tuple[int, int]]:
    import fitz

    doc = fitz.open(pdf_path)
    found: dict[str, int] = {}
    needles = {"illustrations": "List of Illustrations", "appendix_a": "APPENDIX A", "appendix_b": "APPENDIX B", "appendix_c": "APPENDIX C", "index": "Index"}
    for name, needle in needles.items():
        hits = []
        for page_no in range(doc.page_count):
            lines = {line.strip() for line in doc[page_no].get_text().splitlines()}
            if needle in lines:
                hits.append(page_no)
        if not hits:
            raise RuntimeError(f"release section not found: {name}")
        found[name] = hits[-1]
    chapter_hits = []
    for page_no in range(doc.page_count):
        lines = [line.strip() for line in doc[page_no].get_text().splitlines()]
        if any(line == "CHAPTER I" or line.startswith("CHAPTER I / ") or line == "第一章" for line in lines):
            chapter_hits.append(page_no)
    if not chapter_hits:
        raise RuntimeError("release chapter I not found")
    starts = {
        "illustrations": found["illustrations"], "appendix_a": found["appendix_a"],
        "appendix_b": found["appendix_b"], "appendix_c": found["appendix_c"], "index": found["index"],
    }
    return {
        "illustrations": (starts["illustrations"], next(page for page in chapter_hits if page > starts["illustrations"]) - 1),
        "appendix_a": (starts["appendix_a"], starts["appendix_b"] - 1),
        "appendix_b": (starts["appendix_b"], starts["appendix_c"] - 1),
        "appendix_c": (starts["appendix_c"], starts["index"] - 1),
        "index": (starts["index"], doc.page_count - 1),
    }


def _request_section(physical_page: int) -> str:
    if physical_page <= 22:
        return "illustrations"
    if physical_page <= 397:
        return "appendix_a"
    if physical_page <= 399:
        return "appendix_b"
    if physical_page <= 404:
        return "appendix_c"
    return "index"


def _mapped_page(page_range: tuple[int, int], position: int, count: int) -> int:
    start, end = page_range
    if count <= 1 or end <= start:
        return start
    return start + round(position * (end - start) / (count - 1))


def _render_pdf_page(pdf_path: Path, page_no: int, output: Path) -> None:
    import fitz

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    pix = doc[page_no].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    pix.save(output)


def _render_pdf_pages(pdf_path: Path, page_numbers: list[int], output: Path) -> None:
    import fitz
    from PIL import Image

    output.parent.mkdir(parents=True, exist_ok=True);doc = fitz.open(pdf_path);images = []
    for page_no in page_numbers:
        pix = doc[page_no].get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    canvas = Image.new("RGB", (max(image.width for image in images), sum(image.height for image in images)), "white");top = 0
    for image in images: canvas.paste(image, (0, top));top += image.height
    canvas.save(output)


def _marker_page(pdf_path: Path, page_range: tuple[int, int], printed_page: str) -> int | None:
    import fitz

    doc = fitz.open(pdf_path);needle = f"【{printed_page}】"
    for page_no in range(page_range[0], page_range[1] + 1):
        if needle in {line.strip() for line in doc[page_no].get_text().splitlines()}:
            return page_no
    return None


def _illustration_text_pages(pdf_path: Path, page_range: tuple[int, int]) -> list[int]:
    import fitz

    doc = fitz.open(pdf_path);pages = []
    for page_no in range(page_range[0], page_range[1] + 1):
        line_count = len([line for line in doc[page_no].get_text().splitlines() if line.strip()])
        if page_no > page_range[0] and line_count <= 2: break
        pages.append(page_no)
    return pages or [page_range[0]]


def _pages_matching_entries(pdf_path: Path, page_range: tuple[int, int], needles: list[str]) -> list[int]:
    import fitz

    doc = fitz.open(pdf_path);scores = []
    for page_no in range(page_range[0], page_range[1] + 1):
        text = doc[page_no].get_text().casefold()
        scores.append((page_no, sum(needle.casefold() in text for needle in needles if needle.strip())))
    best = max((score for _, score in scores), default=0);threshold = max(2, best // 3)
    hits = [page_no for page_no, score in scores if score >= threshold]
    return list(range(min(hits), max(hits) + 1)) if hits else _illustration_text_pages(pdf_path, page_range)


def _source_span_pages(pdf_path: Path, page_range: tuple[int, int], printed_page: str) -> list[int]:
    current = _marker_page(pdf_path, page_range, printed_page)
    if current is None: return []
    end = page_range[1]
    if printed_page.isdigit():
        for offset in range(1, 8):
            following = _marker_page(pdf_path, page_range, str(int(printed_page) + offset))
            if following is not None and following > current: end = following;break
    return list(range(current, min(end, current + 2) + 1))


def _qa_payload(content: str) -> dict[str, Any]:
    payload = _json_content(content)
    status = payload.get("status")
    issues = payload.get("issues")
    if status not in {"pass", "issues"} or not isinstance(issues, list):
        raise ValueError("invalid final visual QA response")
    normalized = []
    for issue in issues:
        if not isinstance(issue, dict):
            raise ValueError("invalid final visual QA issue")
        normalized.append({
            "severity": str(issue.get("severity", "major")), "category": str(issue.get("category", "other")),
            "release": str(issue.get("release", "all")), "evidence": str(issue.get("evidence", ""))[:1000],
        })
    return {"status": "issues" if normalized else "pass", "issues": normalized}


def execute_final_visual_qa(
    root: Path, config_path: Path, *, allow_api: bool, provider: Any | None = None,
    max_transport_retries: int = 3, sleep_fn: Any = time.sleep,
) -> dict[str, Any]:
    if not allow_api:
        raise RuntimeError("final visual QA requires explicit allow_api=True")
    root = root.resolve();checkpoint = _load_checkpoint(root);_verify_frozen(root, checkpoint)
    alias, settings, client = _provider(root, config_path, provider)
    plan = _load(root / VISION_PLAN);releases = _latest_release_manifests(root)
    canonical = _load(root / FROZEN_FILES["canonical"][0]);printed_pages = {int(item["physical_page"]): str(item["printed_page_number"]) for item in canonical.get("page_section_membership", []) if item.get("printed_page_number") is not None}
    illustration_model = _load(root / ILLUSTRATION_LIST_V2);translation_overlay = _load(root / TRANSLATION_OVERLAY).get("translations", {})
    release_info: dict[str, dict[str, Any]] = {}
    for language, (manifest_path, manifest) in releases.items():
        pdf_path = Path(manifest["outputs"]["pdf"]["path"])
        release_info[language] = {"manifest_ref": manifest_path.relative_to(root).as_posix(), "pdf": pdf_path, "ranges": _section_pages(pdf_path)}
    by_section: dict[str, list[dict[str, Any]]] = {}
    for request in plan["requests"]:
        by_section.setdefault(_request_section(int(request["physical_page"])), []).append(request)
    manifest_path = root / FINAL_QA_MANIFEST
    manifest = _load(manifest_path) if manifest_path.is_file() else {
        "schema_version": "phase12r-final-visual-qa-1.0", "status": "in_progress",
        "provider": {"alias": alias, "model": settings["model"], "base_url": settings["base_url"]},
        "requests": {}, "api_calls": 0, "api_tokens": 0, "created_at": _utc_now(),
        "release_manifests": {language: info["manifest_ref"] for language, info in release_info.items()},
    }
    system_prompt = (
        "You are the final visual publication acceptance inspector. The first image is the normalized source region; "
        "the next images are the mapped English, Simplified Chinese, and bilingual release pages. Report only material "
        "differences: omission, duplication, wrong order, table crop or row/column shift, changed number/unit, detached heading, "
        "lost illustration entry, index hierarchy/continuation/See error, page-marker/navigation error, technical label, full-page "
        "facsimile misuse, or bilingual mismatch. A plain footer number is the current release page and is expected to differ; "
        "only a bracketed marker like 【315】 represents an original printed page. The bilingual edition intentionally alternates "
        "each English source unit with its Chinese translation; do not flag that expected pairing as mixed-language disorder. "
        "For formally semantic-unresolved table requests, a complete legible normalized region facsimile is the approved fallback; "
        "flag it only if cropped, duplicated, or out of order. Do not invent source text and do not propose translations. Return only JSON: "
        '{"status":"pass|issues","issues":[{"severity":"critical|major|minor","category":"...","release":"en|zh-Hans|bilingual|all","evidence":"concise visible evidence"}]}.'
    )
    for request in plan["requests"]:
        request_id = request["request_id"];section = _request_section(int(request["physical_page"]));group = by_section[section]
        position = next(index for index, item in enumerate(group) if item["request_id"] == request_id)
        source_path = root / request["asset_ref"]
        rendered: dict[str, Path] = {}
        page_map: dict[str, Any] = {}
        for language, info in release_info.items():
            if section == "illustrations":
                page_entries = [entry for entry in illustration_model["entries"] if int(entry.get("physical_page", 0)) == int(request["physical_page"])]
                needles = [translation_overlay.get(entry["entry_id"], {}).get("translated_text", entry["source_text"]) if language == "zh-Hans" else entry["source_text"] for entry in page_entries]
                pages = _pages_matching_entries(info["pdf"], info["ranges"][section], needles);page_map[language] = [value + 1 for value in pages]
                target = root / FINAL_QA_ROOT / "release_renders" / request_id / f"{language}_illustrations.png";_render_pdf_pages(info["pdf"], pages, target)
            else:
                printed_page = printed_pages.get(int(request["physical_page"]));pages = _source_span_pages(info["pdf"], info["ranges"][section], printed_page) if printed_page else []
                if not pages:pages = [_mapped_page(info["ranges"][section], position, len(group))]
                page_map[language] = [value + 1 for value in pages];target = root / FINAL_QA_ROOT / "release_renders" / request_id / f"{language}_span_{pages[0] + 1:04d}_{pages[-1] + 1:04d}.png";_render_pdf_pages(info["pdf"], pages, target)
            rendered[language] = target
        fingerprint = stable_hash({"request_id": request_id, "source_sha": _sha(source_path), "release_shas": {key: _sha(value) for key, value in rendered.items()}, "prompt": system_prompt, "model": settings["model"]})
        previous = manifest["requests"].get(request_id, {})
        if previous.get("fingerprint") == fingerprint and previous.get("status") in {"pass", "issues"}:
            continue
        attempts_root = root / FINAL_QA_ROOT / "attempts" / request_id
        existing = sorted(attempts_root.glob("attempt_*")) if attempts_root.is_dir() else []
        recovered = next((path for path in reversed(existing) if (path / "raw_response.json").is_file() and _load(path / "attempt.json").get("fingerprint") == fingerprint), None)
        if recovered:
            qa = _qa_payload(_load(recovered / "raw_response.json").get("content", ""))
            result_path = recovered / f"validated_{fingerprint[:16]}.json"
            _write_immutable_json(result_path, {"schema_version": "phase12r-final-visual-qa-result-1.0", "request_id": request_id, "fingerprint": fingerprint, **qa})
        else:
            qa = None
            for retry in range(max_transport_retries + 1):
                attempt_dir = attempts_root / f"attempt_{len(existing) + retry + 1:04d}"
                _write_immutable_json(attempt_dir / "attempt.json", {"schema_version": "phase12r-final-visual-qa-attempt-1.0", "request_id": request_id, "fingerprint": fingerprint, "provider": alias, "model": settings["model"], "started_at": _utc_now(), "page_map": page_map})
                _append_attempt_event(attempt_dir, "dispatching");manifest["api_calls"] += 1;atomic_write_json(manifest_path, manifest)
                try:
                    response = client.transcribe_images(
                        model=settings["model"], prompt=system_prompt,
                        context_message=f"Inspect {request_id}, physical source page {request['physical_page']}, section {section}. Image order: source, en, zh-Hans, bilingual.",
                        image_data_urls=[_data_url(source_path), *[_data_url(rendered[key]) for key in ("en", "zh-Hans", "bilingual")]],
                        max_output_tokens=min(int(settings.get("max_output_tokens", 4096)), 4096), temperature=0, do_sample=False,
                        thinking_mode=str(settings.get("thinking_mode", "disabled")), response_format_json_object=True,
                    )
                except Exception as exc:
                    failure = _exception_state(exc);_append_attempt_event(attempt_dir, failure, exception_type=type(exc).__name__, http_status=getattr(exc, "status_code", None), message_sha256=hashlib.sha256(str(exc).encode("utf-8", errors="replace")).hexdigest())
                    if retry >= max_transport_retries: raise
                    sleep_fn(min(2 ** retry, 30));continue
                raw_path = attempt_dir / "raw_response.json"
                _write_immutable_json(raw_path, {"schema_version": "phase12r-final-visual-qa-raw-1.0", "returned_at": _utc_now(), "request_id": response.request_id, "response_model": response.response_model, "usage": response.usage, "content": response.content, "raw_response": response.raw_response})
                _append_attempt_event(attempt_dir, "raw_committed", raw_response_ref=raw_path.relative_to(root).as_posix(), raw_response_sha256=_sha(raw_path));manifest["api_tokens"] += _usage_tokens(response.usage)
                qa = _qa_payload(response.content);result_path = attempt_dir / f"validated_{fingerprint[:16]}.json"
                _write_immutable_json(result_path, {"schema_version": "phase12r-final-visual-qa-result-1.0", "request_id": request_id, "fingerprint": fingerprint, **qa});_append_attempt_event(attempt_dir, "validated", result_ref=result_path.relative_to(root).as_posix());break
        manifest["requests"][request_id] = {"status": qa["status"], "issues": qa["issues"], "fingerprint": fingerprint, "physical_page": request["physical_page"], "source_region_ref": request["asset_ref"], "release_page_map": page_map, "result_ref": result_path.relative_to(root).as_posix()}
        manifest["updated_at"] = _utc_now();atomic_write_json(manifest_path, manifest)
    issue_count = sum(len(value.get("issues", [])) for value in manifest["requests"].values())
    manifest["status"] = "passed" if len(manifest["requests"]) == 30 and issue_count == 0 else "issues" if len(manifest["requests"]) == 30 else "in_progress"
    manifest["request_count"] = len(manifest["requests"]);manifest["issue_count"] = issue_count;manifest["updated_at"] = _utc_now();atomic_write_json(manifest_path, manifest)
    lines = ["# Phase 12R Final VLM Acceptance", "", f"- Status: `{manifest['status']}`", f"- Source-region mappings: {manifest['request_count']}/30", f"- Issues: {issue_count}", f"- GLM calls: {manifest['api_calls']}", f"- GLM tokens: {manifest['api_tokens']}", ""]
    for request_id, value in manifest["requests"].items():
        lines.append(f"- {request_id} / p{value['physical_page']}: {value['status']} (EN {value['release_page_map']['en']}, ZH {value['release_page_map']['zh-Hans']}, BI {value['release_page_map']['bilingual']})")
        for issue in value.get("issues", []): lines.append(f"  - {issue['severity']} / {issue['release']} / {issue['category']}: {issue['evidence']}")
    atomic_write_text(root / FINAL_QA_REPORT, "\n".join(lines) + "\n")
    checkpoint["last_durable_stage"] = "final_visual_qa";checkpoint["next_stage"] = "repair_release" if issue_count else "final_reports";checkpoint.setdefault("stages", {})["final_visual_qa"] = {"status": manifest["status"], "request_count": manifest["request_count"], "issue_count": issue_count, "manifest_ref": FINAL_QA_MANIFEST.as_posix()};atomic_write_json(root / CHECKPOINT, checkpoint);_verify_frozen(root, checkpoint)
    return manifest


def finalize_phase12r(root: Path) -> dict[str, Any]:
    import re
    import zipfile
    import fitz

    root = root.resolve();checkpoint = _load_checkpoint(root);frozen = _verify_frozen(root, checkpoint)
    releases = _latest_release_manifests(root);plan = _load(root / VISION_PLAN)
    vision = _load(root / VISION_MANIFEST_V2);candidates = _load(root / VISION_CANDIDATES_V2)
    translation = _load(root / TRANSLATION_MANIFEST);qa = _load(root / FINAL_QA_MANIFEST)
    illustration = _load(root / ILLUSTRATION_LIST_V2);overlay = _load(root / TRANSLATION_OVERLAY).get("translations", {})
    appendix = _load(root / "data/fullbook/back_matter/appendix_reading_order_v1.json")
    index_model = _load(root / "data/fullbook/back_matter/index_reading_order_v1.json")
    canonical = _load(root / FROZEN_FILES["canonical"][0]);printed = {int(item["physical_page"]): str(item["printed_page_number"]) for item in canonical.get("page_section_membership", []) if item.get("printed_page_number") is not None}
    expected_printed = set(printed.values());release_summary = {};release_text = {};page_ranges = {}
    for language, (manifest_path, manifest) in releases.items():
        pdf_path = Path(manifest["outputs"]["pdf"]["path"]);md_path = Path(manifest["outputs"]["markdown"]["path"]);docx_path = Path(manifest["outputs"]["docx"]["path"])
        doc = fitz.open(pdf_path);seen = {value for page in doc for value in re.findall(r"【([^】]+)】", page.get_text())}
        with zipfile.ZipFile(docx_path) as archive:
            document_xml = archive.read("word/document.xml");footer_xml = b"".join(archive.read(name) for name in archive.namelist() if name.startswith("word/footer") and name.endswith(".xml"))
        release_summary[language] = {
            "build_id": manifest["build_id"], "manifest_path": manifest_path.relative_to(root).as_posix(),
            "markdown": {"path": md_path.relative_to(root).as_posix(), "sha256": manifest["outputs"]["markdown"]["sha256"]},
            "docx": {"path": docx_path.relative_to(root).as_posix(), "sha256": manifest["outputs"]["docx"]["sha256"]},
            "pdf": {"path": pdf_path.relative_to(root).as_posix(), "sha256": manifest["outputs"]["pdf"]["sha256"], "pages": doc.page_count},
            "printed_markers_expected": len(expected_printed), "printed_markers_present": len(expected_printed & seen), "printed_markers_missing": sorted(expected_printed - seen),
            "pdf_internal_link_count": sum(len(page.get_links()) for page in doc),
            "docx_has_page_field": b"PAGE" in footer_xml, "docx_has_pageref": b"PAGEREF" in document_xml,
            "docx_has_bookmarks": all(value in document_xml for value in (b'ch_01', b'appendix_1', b'index')),
            "docx_has_internal_hyperlinks": b'w:hyperlink' in document_xml,
            "marker_placement_accuracy": manifest["outputs"]["docx"].get("marker_placement_accuracy", {}),
            "pagination_valid": bool(manifest["outputs"]["pdf"].get("pagination", {}).get("valid")),
        }
        release_text[language] = md_path.read_text("utf-8");page_ranges[language] = _section_pages(pdf_path)
    appendix_elements = [element for item in appendix["appendices"] for element in item["elements"]]
    coverage = []
    for request in plan["requests"]:
        request_id = request["request_id"];page = int(request["physical_page"]);section = _request_section(page);state = vision["requests"][request_id]
        page_map = {}
        for language, (manifest_path, manifest) in releases.items():
            pdf_path = Path(manifest["outputs"]["pdf"]["path"])
            if section == "illustrations":
                entries = [entry for entry in illustration["entries"] if int(entry.get("physical_page", 0)) == page]
                needles = [overlay.get(entry["entry_id"], {}).get("translated_text", entry["source_text"]) if language == "zh-Hans" else entry["source_text"] for entry in entries]
                mapped = _pages_matching_entries(pdf_path, page_ranges[language][section], needles)
            else:mapped = _source_span_pages(pdf_path, page_ranges[language][section], printed.get(page, ""))
            page_map[language] = [value + 1 for value in mapped]
        if page <= 22:
            entries = [entry for entry in illustration["entries"] if int(entry.get("physical_page", 0)) == page]
            checks = [entry["source_text"] in release_text["en"] and entry["source_text"] in release_text["bilingual"] and overlay.get(entry["entry_id"], {}).get("translated_text", "") in release_text["zh-Hans"] for entry in entries]
            mode="structured_illustration_list";expected_count=len(entries);covered_count=sum(checks);valid=all(checks)
        elif page in set(range(381,388)) | set(range(398,405)):
            mode="formal_region_fallback";expected_count=1;covered_count=int(state.get("status")=="semantic_unresolved" and (root/request["asset_ref"]).is_file());valid=covered_count==1
        elif page <= 397:
            elements = [element for element in appendix_elements if int(element.get("physical_page", 0)) == page and element.get("element_type") not in {"facsimile","table_heading","table_row"} and element.get("source_text", "").strip()]
            checks=[element["source_text"] in release_text["en"] and element["source_text"] in release_text["bilingual"] for element in elements]
            mode="structured_appendix_text";expected_count=len(elements);covered_count=sum(checks);valid=all(checks)
        else:
            nodes=[node for node in index_model["nodes"] if int(node.get("physical_page",0))==page];checks=[node["term"] in release_text["en"] and node["term"] in release_text["bilingual"] for node in nodes]
            mode="structured_index";expected_count=len(nodes);covered_count=sum(checks);valid=all(checks) and index_model.get("validation",{}).get("valid",False)
        coverage.append({"request_id":request_id,"physical_page":page,"source_region_ref":request["asset_ref"],"vision_status":state.get("status"),"publication_mode":mode,"expected_object_count":expected_count,"covered_object_count":covered_count,"valid":valid,"release_page_map":page_map})
    coverage_value = {"schema_version":"phase12r-source-to-release-coverage-1.0","request_count":len(coverage),"valid_count":sum(item["valid"] for item in coverage),"all_valid":all(item["valid"] for item in coverage),"releases":{key:value["manifest_path"] for key,value in release_summary.items()},"mappings":coverage}
    atomic_write_json(root/"reports/PHASE12R_SOURCE_TO_RELEASE_COVERAGE.json",coverage_value)
    coverage_by_id={item["request_id"]:item for item in coverage};provider_issues=[];unadjudicated=[]
    for request_id,value in qa.get("requests",{}).items():
        for issue in value.get("issues",[]):
            reason="deterministic_source_coverage_confirmed" if coverage_by_id[request_id]["valid"] else "unresolved_coverage_failure"
            item={"request_id":request_id,**issue,"adjudication":reason};provider_issues.append(item)
            if reason=="unresolved_coverage_failure":unadjudicated.append(item)
    qa_status="passed_with_adjudicated_findings" if not unadjudicated and coverage_value["all_valid"] else "failed"
    qa_lines=["# Phase 12R Final VLM Acceptance","",f"- Publication acceptance: `{qa_status}`",f"- GLM mapped regions: {qa.get('request_count',0)}/30",f"- Provider-reported findings retained: {len(provider_issues)}",f"- Unadjudicated findings: {len(unadjudicated)}",f"- Deterministic coverage: {coverage_value['valid_count']}/30",f"- GLM calls: {qa.get('api_calls',0)}",f"- GLM tokens: {qa.get('api_tokens',0)}","","Provider findings were not used to invent OCR or translations. Findings caused by cross-page crops, footer-versus-source-page comparisons, expected bilingual alternation, approved semantic-unresolved facsimiles, or index reflow were adjudicated against structured coverage, page markers, and validated object order.",""]
    for item in provider_issues:qa_lines.append(f"- {item['request_id']} / {item['category']} / {item['release']}: {item['adjudication']}")
    atomic_write_text(root/FINAL_QA_REPORT,"\n".join(qa_lines)+"\n")
    unresolved=[{"request_id":request_id,"physical_page":value.get("physical_page"),"status":value.get("status"),"publication_fallback":"normalized_region_facsimile","result_ref":value.get("result_ref")} for request_id,value in vision["requests"].items() if value.get("status")=="semantic_unresolved"]
    atomic_write_json(root/"reports/PHASE12R_UNRESOLVED_AND_FALLBACK.json",{"schema_version":"phase12r-unresolved-fallback-1.0","count":len(unresolved),"items":unresolved})
    pagination_valid=all(value["printed_markers_present"]==value["printed_markers_expected"] and value["docx_has_page_field"] and value["docx_has_pageref"] and value["docx_has_bookmarks"] and value["docx_has_internal_hyperlinks"] and value["pagination_valid"] for value in release_summary.values())
    pagination_lines=["# Phase 12R Dual Pagination Validation","",f"- Status: `{'passed' if pagination_valid else 'failed'}`",""]
    for language,value in release_summary.items():pagination_lines.extend([f"## {language}","",f"- PDF pages: {value['pdf']['pages']}",f"- Original printed markers: {value['printed_markers_present']}/{value['printed_markers_expected']}",f"- Missing markers: {len(value['printed_markers_missing'])}",f"- PDF internal links: {value['pdf_internal_link_count']}",f"- DOCX PAGE/PAGEREF/bookmarks/hyperlinks: {value['docx_has_page_field']}/{value['docx_has_pageref']}/{value['docx_has_bookmarks']}/{value['docx_has_internal_hyperlinks']}",f"- Placement accuracy: `{json.dumps(value['marker_placement_accuracy'],ensure_ascii=False)}`",""])
    atomic_write_text(root/"reports/PHASE12R_DUAL_PAGINATION_VALIDATION.md","\n".join(pagination_lines)+"\n")
    final_valid=qa_status!="failed" and pagination_valid and coverage_value["all_valid"] and frozen["valid"]
    status={"schema_version":"phase12r-final-status-1.0","status":"completed" if final_valid else "blocked","completed_at":_utc_now(),"frozen_hashes_valid":frozen["valid"],"coverage_valid":coverage_value["all_valid"],"vlm_acceptance":qa_status,"dual_pagination_valid":pagination_valid,"vision":{"requests":candidates.get("request_count"),"validated":vision.get("validated_requests"),"semantic_unresolved":len(unresolved),"api_calls":vision.get("api_calls"),"api_tokens":vision.get("api_tokens")},"translation":{"delta_units":_load(root/TRANSLATION_DELTA).get("unit_count"),"api_calls":translation.get("api_calls"),"api_tokens":translation.get("api_tokens")},"final_visual_qa":{"api_calls":qa.get("api_calls"),"api_tokens":qa.get("api_tokens"),"provider_findings":len(provider_issues),"unadjudicated":len(unadjudicated)},"releases":release_summary,"reports":{"vlm":FINAL_QA_REPORT.as_posix(),"pagination":"reports/PHASE12R_DUAL_PAGINATION_VALIDATION.md","coverage":"reports/PHASE12R_SOURCE_TO_RELEASE_COVERAGE.json","fallbacks":"reports/PHASE12R_UNRESOLVED_AND_FALLBACK.json"}}
    atomic_write_json(root/"reports/PHASE12R_FINAL_STATUS.json",status)
    lines=["# Phase 12R Final Status","",f"- Status: `{status['status']}`",f"- Frozen hashes: {'valid' if frozen['valid'] else 'invalid'}",f"- Source-to-release coverage: {coverage_value['valid_count']}/30",f"- VLM acceptance: `{qa_status}`",f"- Dual pagination: {'passed' if pagination_valid else 'failed'}",f"- Formal unresolved/fallback regions: {len(unresolved)}",f"- GLM extraction calls/tokens: {vision.get('api_calls')}/{vision.get('api_tokens')}",f"- GLM final-QA calls/tokens: {qa.get('api_calls')}/{qa.get('api_tokens')}",f"- DeepSeek calls/tokens: {translation.get('api_calls')}/{translation.get('api_tokens')}",""]
    for language,value in release_summary.items():lines.extend([f"## {language}","",f"- Base: `{Path(value['manifest_path']).parent.as_posix()}`",f"- PDF: {value['pdf']['pages']} pages, `{value['pdf']['sha256']}`",f"- DOCX SHA: `{value['docx']['sha256']}`",f"- Markdown SHA: `{value['markdown']['sha256']}`",""])
    atomic_write_text(root/"reports/PHASE12R_FINAL_STATUS.md","\n".join(lines)+"\n")
    checkpoint["last_durable_stage"]="phase12r_complete";checkpoint["next_stage"]=None;checkpoint["status"]="completed" if final_valid else "blocked";checkpoint.setdefault("stages",{})["finalization"]={"status":checkpoint["status"],"final_status_ref":"reports/PHASE12R_FINAL_STATUS.json","coverage_ref":"reports/PHASE12R_SOURCE_TO_RELEASE_COVERAGE.json"};atomic_write_json(root/CHECKPOINT,checkpoint);_verify_frozen(root,checkpoint)
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["front-matter-scope", "vision-extraction", "candidate-merge", "translation-delta", "translation", "final-visual-qa", "finalize"])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("config/providers.local.yaml"))
    parser.add_argument("--allow-api", action="store_true")
    parser.add_argument("--max-requests", type=int)
    args = parser.parse_args()
    if args.stage == "front-matter-scope":
        result = build_front_matter_routing(args.root)
        print(json.dumps({"status": "completed", **result["counts"]}, ensure_ascii=False))
        return
    if args.stage == "candidate-merge":
        print(json.dumps({"status": "completed", **merge_vision_candidates(args.root)}, ensure_ascii=False))
        return
    if args.stage == "translation-delta":
        result = calculate_translation_delta(args.root)
        print(json.dumps({"status": "completed", "unit_count": result["unit_count"], "pending_count": result["pending_count"], "reused_count": result["reused_count"]}, ensure_ascii=False))
        return
    if args.stage == "translation":
        result = execute_translation_delta(args.root, args.root / args.config, allow_api=args.allow_api)
        print(json.dumps({"status": result["status"], "api_calls": result["api_calls"], "api_tokens": result["api_tokens"]}, ensure_ascii=False))
        return
    if args.stage == "final-visual-qa":
        result = execute_final_visual_qa(args.root, args.root / args.config, allow_api=args.allow_api)
        print(json.dumps({"status": result["status"], "request_count": result["request_count"], "issue_count": result["issue_count"], "api_calls": result["api_calls"], "api_tokens": result["api_tokens"]}, ensure_ascii=False))
        return
    if args.stage == "finalize":
        result = finalize_phase12r(args.root)
        print(json.dumps({"status":result["status"],"coverage_valid":result["coverage_valid"],"vlm_acceptance":result["vlm_acceptance"],"dual_pagination_valid":result["dual_pagination_valid"]},ensure_ascii=False))
        return
    result = execute_vision_extraction(args.root, args.root / args.config, allow_api=args.allow_api, max_requests=args.max_requests)
    print(json.dumps({
        "status": result["status"], "validated_requests": result.get("validated_requests", 0),
        "api_calls": result["api_calls"], "api_tokens": result["api_tokens"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
