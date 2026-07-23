"""Build the authoritative, non-Canonical appendix reading-order overlay."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json, atomic_write_jsonl
from .translation_units import _unit

APPENDIX_RANGES = {
    "appendix_a": range(381, 398),
    "appendix_b": range(398, 400),
    "appendix_c": range(400, 405),
}
SEMANTIC_TYPES = {"heading", "subheading", "prose", "note", "list_entry", "table_heading", "other_text"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _semantic_kind(kind: str) -> str:
    return "prose" if kind == "other_text" else kind


def build_appendix_reading_order(root: Path) -> dict[str, Any]:
    """Combine page evidence and table evidence without altering Phase 6 Canonical."""
    root = root.resolve()
    canonical = json.loads((root / "data/fullbook/canonical/canonical_book_document_v1.json").read_text("utf-8"))
    evidence = json.loads((root / "data/fullbook/back_matter/back_matter_pages_v1.json").read_text("utf-8"))
    tables = _jsonl(root / "data/fullbook/back_matter/tables.jsonl")
    rows = _jsonl(root / "data/fullbook/back_matter/table_row_groups.jsonl")
    pages = {page["pdf_page"]: page for page in evidence["pages"]}
    page_meta = {page["physical_page"]: page for page in canonical["physical_page_order"]}
    appendix_meta = {item["appendix_id"]: item for item in canonical["appendices"]}
    table_map = {table["table_id"]: table for table in tables}
    rows_by_table_page: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in rows:
        table = table_map.get(row["table_id"])
        if not table:
            raise ValueError(f"orphan table row group: {row['row_group_id']}")
        rows_by_table_page.setdefault((row["table_id"], row["physical_page"]), []).append(row)

    appendices = []
    for appendix_id, expected_pages in APPENDIX_RANGES.items():
        meta = appendix_meta[appendix_id]
        elements: list[dict[str, Any]] = [{
            "element_id": f"{appendix_id}_heading",
            "element_type": "heading",
            "source_object_id": appendix_id,
            "physical_page": meta["physical_page_start"],
            "source_text": " ".join(x for x in (meta.get("title"), meta.get("subtitle")) if x),
            "translation_source": "validated_overlay_or_source",
        }]
        for physical_page in expected_pages:
            page_tables = sorted(
                (table for table in tables if table["appendix_id"] == appendix_id and physical_page in table["source_pages"]),
                key=lambda item: item["table_id"],
            )
            if page_tables:
                # All current appendix tables are row-groups-only. The source image is
                # therefore the column authority; transcripts remain ordered evidence.
                elements.append({
                    "element_id": f"facsimile_p{physical_page:04d}",
                    "element_type": "facsimile",
                    "physical_page": physical_page,
                    "source_page_asset_ref": page_meta[physical_page]["source_page_asset_ref"],
                    "reason": "column_structure_pending_review",
                })
                for table in page_tables:
                    elements.append({
                        "element_id": f"{table['table_id']}_p{physical_page:04d}_heading",
                        "element_type": "table_heading",
                        "physical_page": physical_page,
                        "table_id": table["table_id"],
                        "source_text": f"Source table transcription — page {physical_page}",
                        "parse_status": table["parse_status"],
                    })
                    page_rows = sorted(rows_by_table_page.get((table["table_id"], physical_page), []), key=lambda item: item["row_index"])
                    for row in page_rows:
                        elements.append({
                            "element_id": row["row_group_id"],
                            "element_type": "table_row",
                            "source_object_id": row["row_group_id"],
                            "physical_page": physical_page,
                            "table_id": table["table_id"],
                            "row_index": row["row_index"],
                            "source_text": " | ".join(row.get("raw_ordered_text") or []),
                            "raw_ordered_text": row.get("raw_ordered_text") or [],
                            "parse_status": row.get("parse_status", "pending_review"),
                            "translation_source": "validated_overlay_or_source",
                        })
                continue

            page = pages.get(physical_page)
            if not page:
                raise ValueError(f"appendix source page has no evidence: {physical_page}")
            for item in sorted(page["elements"], key=lambda value: value["reading_order"]):
                kind = item.get("element_type")
                text = str(item.get("text", "")).strip()
                if kind not in SEMANTIC_TYPES or not text or (kind == "heading" and text.upper() == meta["label"]):
                    continue
                elements.append({
                    "element_id": item["element_id"],
                    "element_type": _semantic_kind(kind),
                    "source_object_id": item["element_id"],
                    "physical_page": physical_page,
                    "source_text": text,
                    "source_block_ids": item.get("source_block_ids", []),
                    "translation_source": "pending_appendix_element",
                })
        appendices.append({
            "appendix_id": appendix_id,
            "section_id": meta["section_id"],
            "label": meta["label"],
            "title": meta["title"],
            "subtitle": meta.get("subtitle"),
            "source_pages": list(expected_pages),
            "elements": elements,
        })

    model = {
        "schema_version": "appendix-reading-order-1.0",
        "source_canonical_ref": "data/fullbook/canonical/canonical_book_document_v1.json",
        "source_page_evidence_ref": "data/fullbook/back_matter/back_matter_pages_v1.json",
        "table_refs": ["data/fullbook/back_matter/tables.jsonl", "data/fullbook/back_matter/table_row_groups.jsonl"],
        "appendices": appendices,
    }
    model["validation"] = validate_appendix_model(model)
    return model


def validate_appendix_model(model: dict[str, Any]) -> dict[str, Any]:
    coverage: dict[str, list[int]] = {}
    heading_only = []
    row_count = facsimile_count = 0
    for appendix in model["appendices"]:
        represented = sorted({element["physical_page"] for element in appendix["elements"]})
        coverage[appendix["appendix_id"]] = represented
        body = [element for element in appendix["elements"] if element["element_type"] != "heading"]
        if not body:
            heading_only.append(appendix["appendix_id"])
        row_count += sum(element["element_type"] == "table_row" for element in body)
        facsimile_count += sum(element["element_type"] == "facsimile" for element in body)
    expected = {key: list(value) for key, value in APPENDIX_RANGES.items()}
    missing = {key: sorted(set(value) - set(coverage.get(key, []))) for key, value in expected.items()}
    valid = not any(missing.values()) and not heading_only and row_count > 0 and facsimile_count > 0
    return {
        "valid": valid,
        "source_page_coverage": coverage,
        "missing_source_pages": missing,
        "heading_only_appendices": heading_only,
        "table_row_count": row_count,
        "facsimile_count": facsimile_count,
        "appendix_element_count": sum(len(item["elements"]) for item in model["appendices"]),
    }


def persist_appendix_layer(root: Path) -> dict[str, Any]:
    """Persist the model and add only newly recovered appendix translation units."""
    root = root.resolve()
    model = build_appendix_reading_order(root)
    if not model["validation"]["valid"]:
        raise ValueError(f"invalid appendix model: {model['validation']}")
    model_path = root / "data/fullbook/back_matter/appendix_reading_order_v1.json"
    atomic_write_json(model_path, model)

    unit_path = root / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl"
    state_path = root / "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl"
    units = _jsonl(unit_path)
    states = _jsonl(state_path)
    by_source = {unit["source_object_id"]: unit for unit in units}
    state_ids = {state["translation_unit_id"] for state in states}
    added = []
    appendix_sections = {item["appendix_id"]: item["section_id"] for item in model["appendices"]}
    for appendix in model["appendices"]:
        for element in appendix["elements"]:
            if element["element_type"] not in {"subheading", "prose", "note", "list_entry"}:
                continue
            source_id = element["source_object_id"]
            if source_id in by_source:
                continue
            unit = _unit(
                source_id,
                "appendix_element",
                element["source_text"],
                "pending",
                "translate_appendix_element",
                pages=[element["physical_page"]],
                section=appendix_sections[appendix["appendix_id"]],
                provenance={"appendix_id": appendix["appendix_id"], "source_evidence_ref": "data/fullbook/back_matter/back_matter_pages_v1.json"},
            )
            units.append(unit)
            if unit["translation_unit_id"] not in state_ids:
                states.append({
                    "translation_unit_id": unit["translation_unit_id"],
                    "source_text_sha256": unit["source_text_sha256"],
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                })
            added.append(unit)
    atomic_write_jsonl(unit_path, units)
    atomic_write_jsonl(state_path, states)
    result = {"model_path": str(model_path), "model": model, "added_units": added, "units": units, "states": states}
    _sync_multilingual_metadata(root, result)
    return result


def current_appendix_status_counts(states: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(state["status"] for state in states).items()))


def _sync_multilingual_metadata(root: Path, result: dict[str, Any]) -> None:
    units = result["units"]
    states = result["states"]
    counts = current_appendix_status_counts(states)
    type_counts = dict(sorted(Counter(unit["source_object_type"] for unit in units).items()))
    pending_ids = [state["translation_unit_id"] for state in states if state["status"] == "pending"]

    manifest_path = root / "data/fullbook/multilingual/multilingual_book_manifest_v1.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest.update({
        "translation_unit_count": len(units),
        "unit_type_counts": type_counts,
        "status_counts": counts,
        "pending_translation_count": counts.get("pending", 0),
        "validated_translation_count": counts.get("validated", 0),
        "appendix_reading_order_ref": "data/fullbook/back_matter/appendix_reading_order_v1.json",
        "pending_appendix_element_count": type_counts.get("appendix_element", 0),
    })
    atomic_write_json(manifest_path, manifest)

    document_path = root / "data/fullbook/multilingual/documents/multilingual_book_document_zh-Hans_v1.json"
    document = json.loads(document_path.read_text("utf-8"))
    document["appendix_reading_order_ref"] = manifest["appendix_reading_order_ref"]
    document["pending_appendix_element_count"] = type_counts.get("appendix_element", 0)
    atomic_write_json(document_path, document)

    translation_manifest_path = root / "data/fullbook/multilingual/translation_manifest_zh-Hans_v1.json"
    translation_manifest = json.loads(translation_manifest_path.read_text("utf-8"))
    translation_manifest.update({
        "status_counts": counts,
        "production_translation_status": "ready_for_user_execution",
        "next_action": "translate_pending_appendix_units",
        "pending_appendix_element_count": type_counts.get("appendix_element", 0),
    })
    atomic_write_json(translation_manifest_path, translation_manifest)

    validation_path = root / "data/fullbook/multilingual/reports/multilingual_validation_zh-Hans_v1.json"
    validation = json.loads(validation_path.read_text("utf-8"))
    validation["counts"] = manifest
    validation["appendix_reading_order"] = result["model"]["validation"]
    validation["release_ready"] = False
    validation["release_blockers"] = [f"pending_appendix_elements={len(pending_ids)}"] if pending_ids else []
    atomic_write_json(validation_path, validation)

    checkpoint_path = root / "data/fullbook/multilingual/checkpoints/translation_zh-Hans_production.json"
    checkpoint = json.loads(checkpoint_path.read_text("utf-8"))
    checkpoint.update({
        "status": "resumable" if pending_ids else "completed",
        "completed_at": None if pending_ids else checkpoint.get("completed_at"),
        "next_action": "translate_pending_appendix_units" if pending_ids else "build_first_book_releases",
        "planned_unit_ids": pending_ids,
    })
    atomic_write_json(checkpoint_path, checkpoint)

    phase_path = root / "data/fullbook/checkpoints/phase_9_12_checkpoint.json"
    phase = json.loads(phase_path.read_text("utf-8"))
    phase["translation_status_counts"] = counts
    phase["next_action"] = "translate_pending_appendix_units"
    phase["appendix_full_content_recovery"] = {
        "status": "completed",
        "model_ref": "data/fullbook/back_matter/appendix_reading_order_v1.json",
        "source_page_count": 24,
        "new_pending_units": len(result["added_units"]),
        "table_row_count": result["model"]["validation"]["table_row_count"],
        "facsimile_count": result["model"]["validation"]["facsimile_count"],
        "api_calls": 0,
        "api_tokens": 0,
    }
    atomic_write_json(phase_path, phase)
