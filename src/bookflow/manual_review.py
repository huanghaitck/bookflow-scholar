"""Generic, offline manual/web-assisted review package and patch handling."""

from __future__ import annotations

import hashlib
import json
import shutil
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REVIEW_METHODS = {"automatic", "human", "web_assisted", "human_confirmed"}
REVIEW_STATUSES = {"pending", "reviewed", "confirmed", "rejected", "conflict"}
PATCHABLE_FIELDS = {
    "source_text", "translated_text", "bilingual_text", "source_language", "target_language",
    "zh_text", "bilingual_display", "value_raw", "unit_raw",
    "review_status", "review_method", "review_note", "identification_status",
    "original_romanization", "zh_name", "display_name", "modern_correspondence",
    "evidence_note",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", "utf-8")
    temporary.replace(path)


def load_records(path: Path) -> list[dict[str, Any]]:
    value = _load(path)
    records = value.get("objects", value) if isinstance(value, dict) else value
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("object store must be a JSON array or contain an objects array")
    return records


def validate_patch(patch_path: Path, objects_path: Path) -> dict[str, Any]:
    patch = _load(patch_path)
    records = load_records(objects_path)
    by_id = {item.get("object_id"): item for item in records}
    errors: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    if len(by_id) != len(records) or None in by_id:
        errors.append({"code": "invalid_object_store", "message": "object_id values must exist and be unique"})
    if not isinstance(patch, dict) or patch.get("schema_version") != "manual_patch.v1":
        errors.append({"code": "schema_version", "message": "schema_version must be manual_patch.v1"})
    entries = patch.get("patches", []) if isinstance(patch, dict) else []
    if not isinstance(entries, list):
        errors.append({"code": "patches_type", "message": "patches must be an array"})
        entries = []
    seen: set[str] = set()
    for pos, entry in enumerate(entries):
        where = f"patches[{pos}]"
        if not isinstance(entry, dict):
            errors.append({"code": "entry_type", "message": f"{where} must be an object"})
            continue
        object_id = entry.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            errors.append({"code": "object_id", "message": f"{where}.object_id is required"})
            continue
        if object_id in seen:
            errors.append({"code": "duplicate_patch", "message": f"duplicate patch for {object_id}"})
        seen.add(object_id)
        current = by_id.get(object_id)
        if current is None:
            errors.append({"code": "unknown_object_id", "message": object_id})
            continue
        if entry.get("source_page") != current.get("source_page"):
            conflicts.append({"code": "source_page_mismatch", "object_id": object_id})
        if entry.get("source_file_sha256") != current.get("source_file_sha256"):
            conflicts.append({"code": "source_hash_mismatch", "object_id": object_id})
        if entry.get("base_object_sha256") != object_fingerprint(current):
            conflicts.append({"code": "workspace_version_conflict", "object_id": object_id})
        changes = entry.get("changes")
        if not isinstance(changes, dict) or not changes:
            errors.append({"code": "changes", "message": f"{where}.changes must be a non-empty object"})
        else:
            unknown = sorted(set(changes) - PATCHABLE_FIELDS)
            if unknown:
                errors.append({"code": "unknown_fields", "message": ",".join(unknown)})
            method = changes.get("review_method")
            status = changes.get("review_status")
            if method is not None and method not in REVIEW_METHODS:
                errors.append({"code": "review_method", "message": str(method)})
            if status is not None and status not in REVIEW_STATUSES:
                errors.append({"code": "review_status", "message": str(status)})
    return {
        "valid": not errors and not conflicts,
        "errors": errors,
        "conflicts": conflicts,
        "patch_count": len(entries),
        "patch_sha256": _sha(patch_path),
        "objects_sha256": _sha(objects_path),
    }


def object_fingerprint(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def export_review_package(
    objects_path: Path,
    output_dir: Path,
    *,
    source_pages_pdf: Path | None = None,
    copy_source: bool = False,
    complete: bool = False,
) -> dict[str, Any]:
    records = load_records(objects_path)
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "manual_review_manifest.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "objects_ref": "current_objects.json",
        "objects_sha256": _sha(objects_path),
        "object_count": len(records),
        "source_pages": [],
        "channel": "manual_web_assisted",
        "provider_validation": False,
    }
    shutil.copy2(objects_path, output_dir / "current_objects.json")
    if source_pages_pdf is not None:
        item = {"path": str(source_pages_pdf), "sha256": _sha(source_pages_pdf), "copied": False}
        if copy_source or complete:
            if complete:
                import fitz
                source = fitz.open(source_pages_pdf)
                selected = sorted({int(record["source_page"]) for record in records
                                   if isinstance(record.get("source_page"), int)
                                   and 1 <= int(record["source_page"]) <= source.page_count})
                subset = fitz.open()
                images = output_dir / "source_page_images"; images.mkdir()
                (output_dir / "object_crops").mkdir()
                for page_no in selected:
                    subset.insert_pdf(source, from_page=page_no - 1, to_page=page_no - 1)
                    pix = source[page_no - 1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    pix.save(images / f"page-{page_no:04d}.png")
                if not selected:
                    page = subset.new_page(); page.insert_text((72, 72), "No source pages are pending review.")
                subset.save(output_dir / "source_pages.pdf"); subset.close(); source.close()
            else:
                shutil.copy2(source_pages_pdf, output_dir / "source_pages.pdf")
            item.update({"path": "source_pages.pdf", "copied": True})
        manifest["source_pages"].append(item)
    _atomic_json(output_dir / "manifest.json", manifest)
    _atomic_json(output_dir / "requested_schema.json", manual_patch_schema())
    template = {"schema_version": "manual_patch.v1", "patches": []}
    _atomic_json(output_dir / "manual_patch.json", template)
    _atomic_json(output_dir / "manual_patch.template.json", template)
    _atomic_json(output_dir / "terminology.json", {"terms": [item for record in records for item in record.get("terminology", [])]})
    _atomic_json(output_dir / "place_names.json", {"place_names": [item for record in records for item in record.get("place_names", [])]})
    _atomic_json(output_dir / "current_structure.json", {"objects": [
        {"object_id": item.get("object_id"), "source_page": item.get("source_page"),
         "current_structure": item.get("current_structure"), "issue_type": item.get("issue_type"),
         "review_status": item.get("review_status")} for item in records]})
    for filename, fieldname, values in (
        ("terminology.csv", "term", [item for record in records for item in record.get("terminology", [])]),
        ("place_names.csv", "place_name", [item for record in records for item in record.get("place_names", [])]),
    ):
        with (output_dir / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle); writer.writerow([fieldname])
            for value in values:
                writer.writerow([value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)])
    current_text = []
    current_translation = []
    for item in records:
        current_text.append(f"## {item.get('object_id')}\n\n{item.get('source_text') or item.get('ocr_text') or ''}\n")
        current_translation.append(f"## {item.get('object_id')}\n\n{item.get('translated_text') or item.get('target_text') or ''}\n")
    (output_dir / "current_ocr.md").write_text("\n".join(current_text), "utf-8")
    (output_dir / "current_translation.md").write_text("\n".join(current_translation), "utf-8")
    (output_dir / "instructions.md").write_text(
        "# Manual/web-assisted review\n\n"
        "Review only the supplied source material. Preserve stable object IDs, source pages, "
        "source hashes, raw numbers and units. Use null or a conflict note when uncertain. "
        "Return `manual_patch.json`; web-assisted work is not provider validation.\n",
        "utf-8",
    )
    (output_dir / "copyable_web_prompt.md").write_text(
        "# Review request\n\nUse only the files in this package. Identify uncertain text, "
        "translation, structure, page markers, captions, tables, and footnotes. Return only a "
        "`manual_patch.v1` JSON document matching `requested_schema.json`. Preserve every object ID, "
        "source page, source hash, and base object hash. Mark confirmed work as `web_assisted`; "
        "do not claim automatic provider provenance.\n",
        "utf-8",
    )
    return manifest


def import_patch(
    patch_path: Path,
    objects_path: Path,
    *,
    output_path: Path,
    provenance_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    validation = validate_patch(patch_path, objects_path)
    result = {**validation, "dry_run": dry_run, "imported": False, "already_applied": False}
    if not validation["valid"]:
        return result
    patch = _load(patch_path)
    records = load_records(objects_path)
    by_id = {item["object_id"]: item for item in records}
    history = _load(provenance_path) if provenance_path.is_file() else {"schema_version": "manual_patch_provenance.v1", "imports": []}
    if any(item.get("patch_sha256") == validation["patch_sha256"] for item in history["imports"]):
        result["already_applied"] = True
        return result
    for entry in patch["patches"]:
        by_id[entry["object_id"]].update(entry["changes"])
        by_id[entry["object_id"]]["manual_patch_sha256"] = validation["patch_sha256"]
    if dry_run:
        return result
    payload: Any = {"schema_version": "manual_review_objects.v1", "objects": records}
    _atomic_json(output_path, payload)
    history["imports"].append({
        "patch_sha256": validation["patch_sha256"],
        "base_objects_sha256": validation["objects_sha256"],
        "output_objects_sha256": _sha(output_path),
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "object_ids": [item["object_id"] for item in patch["patches"]],
    })
    _atomic_json(provenance_path, history)
    result.update({"imported": True, "output_sha256": _sha(output_path)})
    return result


def manual_patch_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://bookflow.local/schemas/manual_patch.schema.json",
        "title": "Bookflow manual/web-assisted patch",
        "type": "object",
        "required": ["schema_version", "patches"],
        "properties": {
            "schema_version": {"const": "manual_patch.v1"},
            "patches": {"type": "array", "items": {
                "type": "object",
                "required": ["object_id", "source_page", "source_file_sha256", "base_object_sha256", "changes"],
                "properties": {
                    "object_id": {"type": "string", "minLength": 1},
                    "source_page": {"type": ["integer", "string", "null"]},
                    "source_file_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "base_object_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "changes": {"type": "object", "minProperties": 1},
                },
                "additionalProperties": False,
            }},
        },
        "additionalProperties": False,
    }
