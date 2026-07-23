from __future__ import annotations

import json
from pathlib import Path

from bookflow.manual_review import (
    export_review_package,
    import_patch,
    object_fingerprint,
    validate_patch,
)


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), "utf-8")


def _objects(tmp_path: Path) -> tuple[Path, dict]:
    record = {
        "object_id": "appendix_a.table.001.row.001.cell.001",
        "source_page": 381,
        "source_file_sha256": "a" * 64,
        "source_text": "74 in.",
        "zh_text": "74英寸",
        "review_status": "confirmed",
        "review_method": "human_confirmed",
    }
    path = tmp_path / "objects.json"
    _write_json(path, {"objects": [record]})
    return path, record


def test_manual_patch_validate_import_and_idempotency(tmp_path: Path) -> None:
    objects, record = _objects(tmp_path)
    patch = tmp_path / "patch.json"
    _write_json(patch, {
        "schema_version": "manual_patch.v1",
        "patches": [{
            "object_id": record["object_id"],
            "source_page": 381,
            "source_file_sha256": "a" * 64,
            "base_object_sha256": object_fingerprint(record),
            "changes": {"review_note": "Checked", "review_method": "web_assisted"},
        }],
    })
    assert validate_patch(patch, objects)["valid"]
    output = tmp_path / "imported.json"
    provenance = tmp_path / "provenance.json"
    dry = import_patch(patch, objects, output_path=output, provenance_path=provenance, dry_run=True)
    assert dry["valid"] and not output.exists()
    applied = import_patch(patch, objects, output_path=output, provenance_path=provenance)
    assert applied["imported"]
    assert json.loads(output.read_text("utf-8"))["objects"][0]["review_note"] == "Checked"
    repeated = import_patch(patch, objects, output_path=output, provenance_path=provenance)
    assert repeated["already_applied"]


def test_manual_patch_reports_source_and_version_conflicts(tmp_path: Path) -> None:
    objects, record = _objects(tmp_path)
    patch = tmp_path / "patch.json"
    _write_json(patch, {
        "schema_version": "manual_patch.v1",
        "patches": [{
            "object_id": record["object_id"],
            "source_page": 999,
            "source_file_sha256": "b" * 64,
            "base_object_sha256": "c" * 64,
            "changes": {"review_note": "Conflict"},
        }],
    })
    result = validate_patch(patch, objects)
    assert not result["valid"]
    assert {item["code"] for item in result["conflicts"]} == {
        "source_page_mismatch", "source_hash_mismatch", "workspace_version_conflict"
    }


def test_export_review_package_references_source_by_default(tmp_path: Path) -> None:
    objects, _ = _objects(tmp_path)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"not-real-pdf")
    output = tmp_path / "review"
    manifest = export_review_package(objects, output, source_pages_pdf=source)
    assert manifest["source_pages"][0]["copied"] is False
    assert not (output / "source_pages.pdf").exists()
    assert (output / "requested_schema.json").is_file()

