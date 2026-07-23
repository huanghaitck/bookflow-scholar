from __future__ import annotations

import json
import zipfile
from pathlib import Path

import fitz

from bookflow.batch_backend import BatchBackend


def call(backend: BatchBackend, command: str, payload: dict, command_id: str) -> dict:
    response = backend.execute(command, payload, command_id=command_id, schema_version="1.2")
    assert response["accepted"], response.get("error")
    return response["result"]


def fixture_pdf(path: Path) -> Path:
    document = fitz.open()
    first = document.new_page()
    first.insert_text((50, 70), "Ada Lovelace and Charles Babbage built the Analytical Engine. " * 4)
    second = document.new_page()
    second.insert_text((50, 70), "scan")
    document.save(path)
    document.close()
    return path


def ready_backend(tmp_path: Path) -> tuple[BatchBackend, str, str]:
    backend = BatchBackend(tmp_path / "backend")
    project = call(backend, "createProject", {"name": "Web Assist"}, "create")
    source = fixture_pdf(tmp_path / "fixture.pdf")
    pipeline = {
        "vision_provider_id": "mock", "text_provider_id": "mock", "source_language": "en",
        "target_language": "de", "ocr_mode": "auto", "translation_enabled": True,
        "structure_enabled": False, "output_formats": ["md"],
    }
    imported = call(backend, "importSources", {"project_id": project["project_id"], "paths": [str(source)], "pipeline_config": pipeline}, "import")
    call(backend, "startPipeline", {"batch_id": imported["batch_id"]}, "start")
    snapshot = backend.snapshot(project_id=project["project_id"], batch_id=imported["batch_id"])
    return backend, project["project_id"], snapshot["sources"][0]["source_id"]


def test_glossary_export_validate_diff_apply_and_undo(tmp_path: Path) -> None:
    backend, project_id, source_id = ready_backend(tmp_path)
    created = call(backend, "createWebAssistPackage", {"package_type": "glossary_review", "project_id": project_id, "source_document_id": source_id}, "glossary-create")
    package = created["package"]
    export = Path(package["export_path"])
    required = {"glossary_review.xlsx", "glossary_review.csv", "glossary_review.json",
                "README_FOR_WEB_AI.md", "README_FOR_HUMAN.md", "OFFICIAL_PROMPT.md",
                "PACKAGE_MANIFEST.json"}
    assert required <= {path.name for path in export.iterdir()}
    archive = Path(package["archive_path"])
    assert archive.is_file() and archive.parent == export
    with zipfile.ZipFile(archive) as package_zip:
        assert required <= {Path(name).name for name in package_zip.namelist()}
    assert "Terminologieprüfung" in (export / "OFFICIAL_PROMPT.md").read_text("utf-8")
    manifest = json.loads((export / "PACKAGE_MANIFEST.json").read_text("utf-8"))
    assert manifest["target_language"] == "de"
    payload_path = export / "glossary_review.json"
    payload = json.loads(payload_path.read_text("utf-8"))
    assert payload["items"]
    payload["items"][0]["user_final_translation"] = "Ada Lovelace (reviewed)"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    validation = call(backend, "validateWebAssistImport", {"package_id": package["package_id"], "import_path": str(payload_path), "source_document_id": source_id}, "glossary-validate")
    assert validation["valid"] and validation["changes"] and not validation["conflicts"]
    preview = call(backend, "previewWebAssistDiff", {"package_id": package["package_id"], "source_document_id": source_id}, "glossary-preview")
    assert preview["summary"]["changes"] >= 1
    applied = call(backend, "applyWebAssistImport", {"package_id": package["package_id"], "source_document_id": source_id}, "glossary-apply")
    assert applied["incremental_rebuild"] and applied["undo_available"]
    state = backend.web_assist.get_package(package["package_id"])
    overlay = json.loads(
        (Path(state["applications"][-1]["workspace"]) / "manual_review/imported_objects.json")
        .read_text("utf-8")
    )
    changed = "\n".join(str(item.get("translated_text") or "") for item in overlay["objects"])
    assert changed.count("Ada Lovelace (reviewed)") == 1
    assert "Ada Lovelace" in changed
    undone = call(backend, "undoWebAssistApply", {"project_id": project_id, "source_document_id": source_id}, "glossary-undo")
    assert undone["undone"]
    snapshot = backend.snapshot(project_id=project_id)
    assert snapshot["web_assist_packages"] and snapshot["web_assist_history"]
    event_types = {event["event_type"] for event in backend.events()}
    assert {"web_assist.export_started", "web_assist.export_progress", "web_assist.export_completed", "web_assist.import_started", "web_assist.import_validated", "web_assist.diff_ready", "web_assist.import_completed", "web_assist.corrections_applied", "web_assist.corrections_undone"} <= event_types


def test_difficult_page_package_conflicts_and_import_security(tmp_path: Path) -> None:
    backend, project_id, source_id = ready_backend(tmp_path)
    created = call(backend, "createWebAssistPackage", {"package_type": "difficult_pages", "project_id": project_id, "source_document_id": source_id}, "difficult-create")
    package = created["package"]
    export = Path(package["export_path"])
    assert (export / "difficult_pages_index.xlsx").is_file()
    assert list((export / "pages").glob("*.png"))
    assert Path(package["archive_path"]).is_file()
    assert "multimodales Modell" in (export / "OFFICIAL_PROMPT.md").read_text("utf-8")
    payload_path = export / "difficult_pages_index.json"
    payload = json.loads(payload_path.read_text("utf-8"))
    assert payload["items"]
    first = payload["items"][0]
    object_row = first["objects"][0]
    corrections = [{
        "source_object_id": object_row["source_object_id"],
        "translation_unit_id": object_row["translation_unit_id"],
        "source_text_sha256": object_row["source_text_sha256"],
        "translated_text_sha256": object_row["translated_text_sha256"],
        "corrected_source_text": "Reviewed page text.",
        "corrected_translated_text": "[mock] Reviewed page text.",
        "structure_note": "",
        "review_status": "resolved",
    }]
    answer = export / "pages" / f"{first['page_item_id']}.answer.md"
    answer.write_text("# Structured object corrections\n\n```json\n"
                      + json.dumps(corrections, ensure_ascii=False, indent=2)
                      + "\n```\n", "utf-8")
    answer_validation = call(backend, "validateWebAssistImport", {"package_id": package["package_id"], "import_path": str(export), "source_document_id": source_id}, "difficult-answer")
    assert answer_validation["valid"] and answer_validation["changes"]
    applied = call(backend, "applyWebAssistImport", {
        "package_id": package["package_id"], "source_document_id": source_id,
    }, "difficult-apply")
    state = backend.web_assist.get_package(package["package_id"])
    overlay = json.loads(
        (Path(state["applications"][-1]["workspace"]) / "manual_review/imported_objects.json")
        .read_text("utf-8")
    )
    assert len(overlay["objects"]) == 1
    assert overlay["objects"][0]["source_text"] == "Reviewed page text."
    wrong_source = backend.execute("previewWebAssistDiff", {
        "package_id": package["package_id"], "source_document_id": "source_wrong",
    }, command_id="wrong-source", schema_version="1.2")
    assert not wrong_source["accepted"]
    payload["items"][0]["user_corrected_markdown"] = "Unstructured whole-page answer."
    payload["items"][0]["object_corrections"] = []
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    unstructured = call(backend, "validateWebAssistImport", {
        "package_id": package["package_id"], "import_path": str(payload_path),
        "source_document_id": source_id,
    }, "difficult-unstructured")
    assert not unstructured["valid"]
    assert "unstructured_page_answer" in {
        item["conflict_type"] for item in unstructured["conflicts"]
    }
    payload["items"][0]["source_hash"] = "wrong-hash"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    validation = call(backend, "validateWebAssistImport", {"package_id": package["package_id"], "import_path": str(payload_path), "source_document_id": source_id}, "difficult-conflict")
    assert not validation["valid"] and validation["conflicts"]
    forbidden = export / "payload.exe"
    forbidden.write_bytes(b"not executable")
    rejected = backend.execute("validateWebAssistImport", {"package_id": package["package_id"], "import_path": str(export), "source_document_id": source_id}, command_id="security", schema_version="1.2")
    assert not rejected["accepted"] and rejected["error"]["error_code"] == "command_rejected"
    assert "web_assist.import_failed" in {event["event_type"] for event in backend.events()}
    discarded = call(backend, "discardWebAssistPackage", {"package_id": package["package_id"], "source_document_id": source_id}, "discard")
    assert discarded["discarded"]


def test_empty_review_sets_do_not_create_packages(tmp_path: Path, monkeypatch) -> None:
    backend, project_id, source_id = ready_backend(tmp_path)
    monkeypatch.setattr(backend.web_assist, "_glossary_items", lambda *args, **kwargs: [])
    glossary = call(backend, "createWebAssistPackage", {
        "package_type": "glossary_review", "project_id": project_id,
        "source_document_id": source_id,
    }, "empty-glossary")
    assert glossary == {
        "package": None, "files": [], "skipped": True, "reason": "no_glossary_items",
    }
    monkeypatch.setattr(backend.web_assist, "_difficult_items", lambda *args, **kwargs: [])
    difficult = call(backend, "createWebAssistPackage", {
        "package_type": "difficult_pages", "project_id": project_id,
        "source_document_id": source_id,
    }, "empty-difficult")
    assert difficult == {
        "package": None, "files": [], "skipped": True, "reason": "no_difficult_pages",
    }
    assert not list((tmp_path / "backend" / "web_assist" / "exports").iterdir())
    assert not backend.snapshot(project_id=project_id)["web_assist_packages"]


def test_capabilities_and_deferred_folder_command_are_honest(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    capabilities = backend.capabilities()
    assert all(capabilities[name] for name in (
        "supportsWebAssist", "supportsGlossaryReviewExport", "supportsGlossaryReviewImport",
        "supportsDifficultPageExport", "supportsDifficultPageImport", "supportsWebAssistDiffPreview",
        "supportsIncrementalRebuild", "supportsWebAssistUndo",
    ))
    assert "openWebAssistPackageFolder" in capabilities["transport_deferred_commands"]
    response = backend.execute("openWebAssistPackageFolder", {}, command_id="deferred-folder", schema_version="1.2")
    assert not response["accepted"] and response["error"]["error_code"] == "transport_deferred"
