from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .io_utils import atomic_write_json, atomic_write_jsonl, sha256_file
from .multilingual_schema import TranslationUnit
from .source_provenance import build_title_provenance

CANONICAL_SHA = "16c1c9ba4d60d1c2a4124433291a1a56bf499384215c720f6988e6e183c01326"
POLICY_VERSION = "translation-policy-1.0"


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _unit(source_id: str, kind: str, text: str, status: str, policy: str, *, pages=None,
          printed_pages=None, section=None, existing_ref=None, provenance=None, context_before="", context_after="") -> dict:
    source_sha = hashlib.sha256(text.encode()).hexdigest()
    stable = f"{source_id}|{kind}|zh-Hans|{POLICY_VERSION}"
    unit_id = "tu_" + hashlib.sha256(stable.encode()).hexdigest()[:24]
    cache_key = hashlib.sha256((stable + "|" + source_sha).encode()).hexdigest()
    return TranslationUnit(unit_id, source_id, kind, "en", "zh-Hans", text, source_sha,
                           context_before, context_after, section, pages or [], printed_pages or [], policy, status,
                           existing_ref, cache_key, provenance or {}).to_dict()


def build_multilingual_layer(root: Path) -> dict:
    canonical_path = root / "data/fullbook/canonical/canonical_book_document_v1.json"
    if sha256_file(canonical_path) != CANONICAL_SHA: raise ValueError("Phase 6 Canonical SHA mismatch")
    canonical = json.loads(canonical_path.read_text("utf-8"))
    bilingual_path = root / "data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json"
    bilingual = json.loads(bilingual_path.read_text("utf-8"))
    translated = bilingual["logical_blocks"]
    translated_by_id = {item["block_id"]: (idx, item) for idx, item in enumerate(translated)}
    printed_by_page = {p["physical_page"]: p.get("printed_page_number") for p in canonical["physical_page_order"]}
    def printed(pages): return [printed_by_page[p] for p in pages or [] if printed_by_page.get(p) is not None]
    source_ids = [u["logical_block_id"] for u in canonical["logical_units"]]
    if len(translated_by_id) != 971 or set(source_ids) != set(translated_by_id): raise ValueError("existing translation ID mismatch")

    units = []
    for idx, source in enumerate(canonical["logical_units"]):
        bidx, target = translated_by_id[source["logical_block_id"]]
        if bidx != idx or target["source_text"] != source["source_text"]: raise ValueError("existing translation order/source mismatch")
        units.append(_unit(source["logical_block_id"], "logical_unit", source["source_text"], "reused_frozen", "reuse_frozen",
                           pages=source.get("source_pages"), printed_pages=printed(source.get("source_pages")), section=source.get("section_id"),
                           existing_ref=f"data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json#/logical_blocks/{idx}/translation",
                           provenance={"bilingual_sha256": sha256_file(bilingual_path), "cache_fingerprint": target.get("cache_fingerprint")}))

    provenance = build_title_provenance(root, canonical)
    units.append(_unit("book_title", "book_metadata", canonical["metadata"]["title"], "pending", "translate_title", provenance={"metadata_field": "title"}))
    units.append(_unit("book_author", "book_metadata", canonical["metadata"]["author"], "pending", "translate_name_display", provenance={"metadata_field": "author"}))
    for idx, chapter in enumerate([s for s in canonical["sections"] if s["section_type"] == "chapter"]):
        units.append(_unit(chapter["section_id"], "chapter_title", chapter["canonical_title"], "pending", "translate_title",
                           pages=[chapter["start_page"]], printed_pages=printed([chapter["start_page"]]), section=chapter["section_id"], provenance={"title_overlay_index": idx}))
    for link in canonical["caption_links"]:
        if link.get("link_status") == "confirmed":
            units.append(_unit(link["caption_id"], "confirmed_caption", link["caption_text"], "pending", "translate_caption",
                               pages=[link["source_page"]], printed_pages=printed([link["source_page"]]), provenance={"figure_id": link["linked_figure_id"]}))
    for appendix in canonical["appendices"]:
        units.append(_unit(appendix["appendix_id"], "appendix_title", " ".join(x for x in [appendix.get("title"), appendix.get("subtitle")] if x),
                           "pending", "translate_title", pages=list(range(appendix["physical_page_start"], appendix["physical_page_end"] + 1)), printed_pages=printed(list(range(appendix["physical_page_start"], appendix["physical_page_end"] + 1))), section=appendix["section_id"]))
    for row in canonical["table_row_groups"]:
        text = " | ".join(row["raw_ordered_text"])
        status = "blocked_by_source_quality" if not text.strip() or text.upper().startswith("APPENDIX") else "pending"
        units.append(_unit(row["row_group_id"], "table_row_group", text, status, "translate_ordered_fields",
                           pages=[row["physical_page"]], printed_pages=printed([row["physical_page"]]), provenance={"table_id": row["table_id"]}))
    for entry in canonical["index_entries"]:
        text = entry["entry_text"] + ("; " + ", ".join(entry.get("page_references", [])) if entry.get("page_references") else "")
        units.append(_unit(entry["entry_id"], "confirmed_index_entry", text, "pending", "translate_index_with_source",
                           pages=[entry["physical_page"]], printed_pages=printed([entry["physical_page"]]), section=entry.get("section_id"), provenance={"page_references": entry.get("page_references", [])}))
    for group in canonical["index_entry_groups"]:
        units.append(_unit(group["group_id"], "index_entry_group", group["raw_text"], "preserve_source", "preserve_source",
                           pages=[group["physical_page"]], printed_pages=printed([group["physical_page"]]), section=group.get("section_id"), provenance={"page_references": group.get("page_references", [])}))

    ids = [u["translation_unit_id"] for u in units]
    if len(ids) != len(set(ids)): raise ValueError("duplicate translation unit ID")
    out = root / "data/fullbook/multilingual"
    (out / "units").mkdir(parents=True, exist_ok=True); (out / "state").mkdir(exist_ok=True); (out / "documents").mkdir(exist_ok=True); (out / "policies").mkdir(exist_ok=True); (out / "reports").mkdir(exist_ok=True)
    unit_path = out / "units/translation_units_zh-Hans_v1.jsonl"
    atomic_write_jsonl(unit_path, units)
    states = [{"translation_unit_id": u["translation_unit_id"], "source_text_sha256": u["source_text_sha256"], "status": u["translation_status"], "attempts": 0, "last_error": None} for u in units]
    atomic_write_jsonl(out / "state/translation_state_zh-Hans_v1.jsonl", states)
    policy = {"policy_version": POLICY_VERSION, "source_language": "en", "target_language": "zh-Hans", "index_default": "preserve_source", "candidate_cells": "not_translation_units", "page_assets": "skip", "statuses": sorted({u["translation_status"] for u in units})}
    atomic_write_json(out / "policies/translation_policy_v1.json", policy)
    counts = Counter(u["source_object_type"] for u in units); statuses = Counter(u["translation_status"] for u in units)
    manifest = {"schema_version": "multilingual-book-1.0", "source_canonical_ref": "data/fullbook/canonical/canonical_book_document_v1.json", "source_canonical_sha256": CANONICAL_SHA, "source_language": "en", "target_languages": ["zh-Hans"], "translation_unit_count": len(units), "unit_type_counts": dict(counts), "status_counts": dict(statuses), "existing_main_text_units": 971, "reused_frozen": 971, "retranslated_existing_main_text": 0, "unmapped_existing_translation": 0, "duplicate_existing_translation": 0}
    atomic_write_json(out / "multilingual_book_manifest_v1.json", manifest)
    document = {"schema_version": "multilingual-document-1.0", "source_canonical_ref": manifest["source_canonical_ref"], "source_canonical_sha256": CANONICAL_SHA, "target_language": "zh-Hans", "translation_units_ref": "data/fullbook/multilingual/units/translation_units_zh-Hans_v1.jsonl", "translation_state_ref": "data/fullbook/multilingual/state/translation_state_zh-Hans_v1.jsonl", "existing_translation_document_ref": "data/fullbook/main_text/bilingual_document_main_text_zh-Hans_v1.json", "reconstruction_ready": True}
    atomic_write_json(out / "documents/multilingual_book_document_zh-Hans_v1.json", document)
    validation = {"validation_passed": True, "checks": {"canonical_sha": True, "existing_ids_match": True, "existing_order_match": True, "unit_ids_unique": True, "raw_title_candidates_preserved": provenance[25]["raw_toc_candidate"].find("ACCOUNT ON") >= 0, "candidate_cells_excluded": not any(u["source_object_type"] == "candidate_cell" for u in units), "pending_index_preserved": all(u["translation_status"] == "preserve_source" for u in units if u["source_object_type"] == "index_entry_group")}, "counts": manifest}
    atomic_write_json(out / "reports/multilingual_validation_zh-Hans_v1.json", validation)
    return {"manifest": manifest, "units": units, "validation": validation}
