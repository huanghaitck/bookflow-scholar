"""Reconstruct the four-page printed index into a hierarchical reading order."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .io_utils import atomic_write_json, atomic_write_jsonl
from .translation_units import _unit

INDEX_PAGES = range(405, 409)
REF_TOKEN = r"(?:[ivxlcdm]+\.?|\d+(?:[-–]\d+)?)"
REFS_RE = re.compile(rf",\s*({REF_TOKEN}(?:\s*,\s*{REF_TOKEN})*)\s*$", re.I)
PURE_REFS_RE = re.compile(rf"^[\s,.;]*{REF_TOKEN}(?:\s*,\s*{REF_TOKEN})*[\s,.;]*$", re.I)

CORRECTIONS = {
    "Budorcaa bedfordi": "Budorcas bedfordi",
    "ainensis": "sinensis",
    "Capreoltis bedfordi": "Capreolus bedfordi",
    "Fukiang-fii": "Fukiang-fu",
    "Gazella przewalskii": "Gazella przewalskii",
    "Kia-yii-kwan": "Kia-yü-kwan",
    "Rhinopithecus roxellance": "Rhinopithecus roxellanae",
    "Riitimeyer": "Rütimeyer",
    "Vrotragus goral": "Urotragus goral",
    "1 6": "16",
    "1 54": "154",
    "1 67": "167",
    "1 70": "170",
    "1 84": "184",
}

# The five printed Serow subentries share the same x position on p407 right.
# The generic indentation thresholds otherwise make the first one level 1 and
# the remaining four level 2 merely because the level-1 stack now exists.
SEROW_SUBENTRIES = {"appearance of", "driving", "hunting", "numbers of", "stalking"}


def _clean(text: str) -> str:
    text = " ".join(text.replace("\u00ad", "").split())
    for old, new in CORRECTIONS.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    return text.strip(" —")


def _page_lines(page: Any, physical_page: int) -> list[dict[str, Any]]:
    fragments: dict[str, list[tuple[float, float, float, float, str]]] = {"left": [], "right": []}
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            column = "left" if x0 < 145 else "right"
            fragments[column].append((x0, y0, x1, y1, text))
    output = []
    for column in ("left", "right"):
        ordered = sorted(fragments[column], key=lambda item: (item[1], item[0]))
        clusters: list[list[tuple[float, float, float, float, str]]] = []
        for fragment in ordered:
            if clusters and abs(fragment[1] - sum(item[1] for item in clusters[-1]) / len(clusters[-1])) <= 4.5:
                clusters[-1].append(fragment)
            else:
                clusters.append([fragment])
        for ordinal, cluster in enumerate(clusters):
            text = _clean(" ".join(item[4] for item in sorted(cluster, key=lambda item: item[0])))
            x0 = min(item[0] for item in cluster); y0 = min(item[1] for item in cluster)
            x1 = max(item[2] for item in cluster); y1 = max(item[3] for item in cluster)
            if not text or text == "INDEX" or text.isdigit() or "Printed by Hazell" in text:
                continue
            output.append({
                "source_line_id": f"idxline_p{physical_page:04d}_{column}_{ordinal:03d}",
                "physical_page": physical_page,
                "printed_page": physical_page - 90,
                "column": column,
                "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                "x0": x0,
                "text": text,
            })
    return output


def _parse_display(text: str) -> tuple[str, list[str], str | None]:
    cross = None
    if re.search(r"\.\s+See\s+", text, re.I):
        term, target = re.split(r"\.\s+See\s+", text, maxsplit=1, flags=re.I)
        return term.strip(), [], target.strip().rstrip(".")
    match = REFS_RE.search(text)
    if not match:
        return text.strip().rstrip("."), [], cross
    refs = [token.strip().rstrip(".") for token in match.group(1).split(",")]
    return text[:match.start()].strip().rstrip("."), refs, cross


def build_index_reading_order(root: Path) -> dict[str, Any]:
    import fitz

    root = root.resolve()
    pdf_path = root / "input/The big game of central and western China (1913).pdf"
    canonical = json.loads((root / "data/fullbook/canonical/canonical_book_document_v1.json").read_text("utf-8"))
    assets = {page["physical_page"]: page["source_page_asset_ref"] for page in canonical["physical_page_order"]}
    doc = fitz.open(pdf_path)
    lines = [line for physical in INDEX_PAGES for line in _page_lines(doc[physical - 1], physical)]
    doc.close()

    nodes: list[dict[str, Any]] = []
    stack: dict[int, str] = {}
    current_main_term = ""
    baselines = {405: {"left": 10, "right": 150}, 406: {"left": 24, "right": 162}, 407: {"left": 11, "right": 149}, 408: {"left": 27, "right": 160}}
    for line in lines:
        text = line["text"]
        if PURE_REFS_RE.fullmatch(text) and nodes:
            nodes[-1]["source_display_text"] = _clean(nodes[-1]["source_display_text"] + " " + text)
            nodes[-1]["source_line_ids"].append(line["source_line_id"])
            continue
        base = baselines[line["physical_page"]][line["column"]]
        delta = line["x0"] - base
        level = 2 if delta >= 18 else 1 if delta >= 8 else 0
        begins_lower = bool(re.match(r"^[a-z]", text))
        if begins_lower and level == 0 and nodes:
            nodes[-1]["source_display_text"] = _clean(nodes[-1]["source_display_text"] + " " + text)
            nodes[-1]["source_line_ids"].append(line["source_line_id"])
            continue
        # Printed continuations repeat the headword at a new page/column.
        if text.startswith("Thibetans, customs") and current_main_term == "Thibetans":
            text = text[len("Thibetans, "):]; level = 1
        if text.startswith("Wapiti, numbers") and current_main_term == "Wapiti":
            text = text[len("Wapiti, "):]; level = 1
        if (
            line["physical_page"] == 407
            and line["column"] == "right"
            and current_main_term == "Serow, white-maned"
            and _parse_display(text)[0].lower() in SEROW_SUBENTRIES
        ):
            level = 1
        if level > 0 and not nodes:
            level = 0
        parent_id = stack.get(level - 1) if level else None
        if level and not parent_id:
            parent_id = stack.get(0)
            level = 1
        node_id = f"index_node_{len(nodes) + 1:04d}"
        node = {
            "index_node_id": node_id,
            "physical_page": line["physical_page"],
            "printed_page": line["printed_page"],
            "column": line["column"],
            "indent_level": level,
            "parent_id": parent_id,
            "source_display_text": text,
            "source_line_ids": [line["source_line_id"]],
            "source_bbox": line["bbox"],
        }
        nodes.append(node)
        stack[level] = node_id
        for deeper in tuple(key for key in stack if key > level):
            stack.pop(deeper, None)
        if level == 0:
            current_main_term = _parse_display(text)[0]

    # Parse after continuations have been joined.
    for node in nodes:
        term, refs, cross = _parse_display(node["source_display_text"])
        node.update(term=term, page_references=refs, cross_reference=cross)
    # A main-entry wrap can begin at base indentation with lower-case text; ensure
    # every surviving node is a meaningful term rather than an OCR fragment.
    forbidden_when_unparented = {"appearance of", "young", "of"}
    orphan_nodes = [
        node["index_node_id"]
        for node in nodes
        if not node["term"]
        or node["term"].isdigit()
        or (node["indent_level"] and not node["parent_id"])
        or (node["term"].lower() in forbidden_when_unparented and not node["parent_id"])
    ]
    duplicate_keys = [key for key, count in Counter((node["parent_id"], node["term"], tuple(node["page_references"]), node["cross_reference"]) for node in nodes).items() if count > 1]
    expected_source_line_ids = [line["source_line_id"] for line in lines]
    represented_source_line_ids = [source_id for node in nodes for source_id in node["source_line_ids"]]
    source_line_counts = Counter(represented_source_line_ids)
    duplicate_source_line_ids = sorted(source_id for source_id, count in source_line_counts.items() if count > 1)
    source_line_ids = set(represented_source_line_ids)
    missing_source_line_ids = sorted(set(expected_source_line_ids) - source_line_ids)

    def normalized_term(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    main_terms = [normalized_term(node["term"]) for node in nodes if node["indent_level"] == 0]
    unresolved_cross_reference_node_ids = []
    for node in nodes:
        if not node["cross_reference"]:
            continue
        target = normalized_term(node["cross_reference"])
        if not any(term == target or term.startswith(target + " ") for term in main_terms):
            unresolved_cross_reference_node_ids.append(node["index_node_id"])
    reading_order_valid = represented_source_line_ids == expected_source_line_ids
    validation_valid = (
        not orphan_nodes
        and not duplicate_keys
        and not duplicate_source_line_ids
        and not missing_source_line_ids
        and not unresolved_cross_reference_node_ids
        and reading_order_valid
    )
    model = {
        "schema_version": "index-reading-order-1.0",
        "source_pdf_ref": "input/The big game of central and western China (1913).pdf",
        "source_pages": list(INDEX_PAGES),
        "page_assets": {str(page): assets[page] for page in INDEX_PAGES},
        "nodes": nodes,
        "legacy_candidate_provenance": {
            "source_unit_ref": "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl",
            "source_object_type": "index_entry_group",
            "translation_status": "preserve_source",
            "expected_count": 334,
            "disposition": "retained_unchanged_as_candidate_evidence",
        },
        "validation": {
            "valid": validation_valid,
            "source_page_coverage": list(INDEX_PAGES),
            "source_line_count": len(lines),
            "represented_source_line_count": len(source_line_ids),
            "missing_source_elements": len(missing_source_line_ids),
            "missing_source_line_ids": missing_source_line_ids,
            "duplicate_source_mapping_count": len(duplicate_source_line_ids),
            "duplicate_source_line_ids": duplicate_source_line_ids,
            "reading_order_valid": reading_order_valid,
            "orphan_node_ids": orphan_nodes,
            "duplicate_node_keys": [list(key) for key in duplicate_keys],
            "cross_reference_count": sum(bool(node["cross_reference"]) for node in nodes),
            "unresolved_cross_reference_count": len(unresolved_cross_reference_node_ids),
            "unresolved_cross_reference_node_ids": unresolved_cross_reference_node_ids,
            "main_entry_count": sum(node["indent_level"] == 0 for node in nodes),
            "subentry_count": sum(node["indent_level"] > 0 for node in nodes),
            "continuation_count": len(lines) - len(nodes),
            "node_count": len(nodes),
        },
    }
    return model


def persist_index_layer(root: Path) -> dict[str, Any]:
    root = root.resolve()
    model = build_index_reading_order(root)
    if not model["validation"]["valid"]:
        raise ValueError(f"invalid index reconstruction: {model['validation']}")
    model_path = root / "data/fullbook/back_matter/index_reading_order_v1.json"
    atomic_write_json(model_path, model)
    legacy_unit_path = root / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl"
    legacy_units = [json.loads(line) for line in legacy_unit_path.read_text("utf-8").splitlines() if line]
    legacy_candidates = [
        unit for unit in legacy_units
        if unit["source_object_type"] == "index_entry_group" and unit["translation_status"] == "preserve_source"
    ]
    if len(legacy_candidates) != 334:
        raise ValueError(f"legacy index provenance count mismatch: {len(legacy_candidates)}")
    units = []
    for node in model["nodes"]:
        source_id = node["index_node_id"]
        parent = next((candidate for candidate in model["nodes"] if candidate["index_node_id"] == node["parent_id"]), None)
        units.append(_unit(
            source_id,
            "reconstructed_index_entry",
            node["term"],
            "pending",
            "translate_index_bilingual_label_preserve_latin",
            pages=[node["physical_page"]],
            printed_pages=[node["printed_page"]],
            section="bm_index",
            context_before=parent["term"] if parent else "",
            provenance={
                "source_line_ids": node["source_line_ids"],
                "page_references": node["page_references"],
                "cross_reference": node["cross_reference"],
                "parent_index_node_id": node["parent_id"],
                "legacy_candidate_groups_preserved": 334,
                "legacy_candidate_unit_ref": "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl",
            },
        ))
    unit_path = root / "data/fullbook/multilingual/units/translation_units_index_zh-Hans_v1.jsonl"
    atomic_write_jsonl(unit_path, units)
    return {"model": model, "model_path": str(model_path), "unit_path": str(unit_path), "added_units": units}


def merge_index_translation_queue(root: Path) -> dict[str, Any]:
    """Append the validated index-unit plan to the formal queue, then reconcile."""
    from .translation_runner import TranslationRunner

    root = root.resolve()
    model = json.loads((root / "data/fullbook/back_matter/index_reading_order_v1.json").read_text("utf-8"))
    if not model.get("validation", {}).get("valid"):
        raise ValueError("formal index model is not valid")
    index_path = root / "data/fullbook/multilingual/units/translation_units_index_zh-Hans_v1.jsonl"
    formal_path = root / "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl"
    index_units = [json.loads(line) for line in index_path.read_text("utf-8").splitlines() if line]
    formal_units = [json.loads(line) for line in formal_path.read_text("utf-8").splitlines() if line]
    if len(index_units) != model["validation"]["node_count"]:
        raise ValueError("index translation unit count mismatch")
    if any(unit["translation_status"] != "pending" for unit in index_units):
        raise ValueError("new index translation units must be pending")
    legacy = [unit for unit in formal_units if unit["source_object_type"] == "index_entry_group" and unit["translation_status"] == "preserve_source"]
    if len(legacy) != model["legacy_candidate_provenance"]["expected_count"]:
        raise ValueError("legacy preserve_source provenance mismatch")
    formal_by_id = {unit["translation_unit_id"]: unit for unit in formal_units}
    if len(formal_by_id) != len(formal_units):
        raise ValueError("duplicate formal translation unit ID")
    conflicts = [unit["translation_unit_id"] for unit in index_units if unit["translation_unit_id"] in formal_by_id and formal_by_id[unit["translation_unit_id"]] != unit]
    if conflicts:
        raise ValueError(f"conflicting index translation units: {conflicts[:5]}")
    added = [unit for unit in index_units if unit["translation_unit_id"] not in formal_by_id]
    if added:
        atomic_write_jsonl(formal_path, [*formal_units, *added])
    runner = TranslationRunner(root)
    reconciliation = runner.reconcile()
    checkpoint = json.loads(runner.checkpoint_path.read_text("utf-8"))
    states = runner._load_states()
    checkpoint["planned_unit_ids"] = sorted(state["translation_unit_id"] for state in states if state["status"] == "pending")
    atomic_write_json(runner.checkpoint_path, checkpoint)
    return {
        "added_unit_count": len(added),
        "formal_unit_count": len(formal_units) + len(added),
        "legacy_preserve_source_count": len(legacy),
        "reconciliation": reconciliation,
    }
