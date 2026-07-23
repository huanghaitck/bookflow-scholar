from __future__ import annotations

import json
from pathlib import Path

import fitz

from bookflow.batch_backend import BatchBackend, CONTRACT_VERSION


SCHEMAS = Path(__file__).parents[1] / "contracts/v1_2"


def pdf(path: Path, text: str = "Contract test\nParagraph one.\nParagraph two.") -> Path:
    document = fitz.open(); page = document.new_page(); page.insert_text((50, 70), text); document.save(path); document.close(); return path


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text("utf-8"))


def validate(value: dict, specification: dict) -> None:
    assert set(specification.get("required", [])) <= set(value)
    for key, rule in specification.get("properties", {}).items():
        if key not in value: continue
        if "const" in rule: assert value[key] == rule["const"]
        if "enum" in rule: assert value[key] in rule["enum"]
        kind = rule.get("type")
        if kind == "object": assert isinstance(value[key], dict)
        if kind == "array": assert isinstance(value[key], list)
        if kind == "string": assert isinstance(value[key], str)
        if kind == "boolean": assert isinstance(value[key], bool)
        if kind == "integer": assert isinstance(value[key], int)


def call(backend: BatchBackend, command: str, payload: dict, identifier: str) -> dict:
    response = backend.execute(command, payload, command_id=identifier, schema_version="1.2")
    validate(response, schema("BACKEND_COMMAND_RESPONSE_V1_2.schema.json")); return response


def test_all_direct_commands_project_lifecycle_and_schemas(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    capabilities_response = call(backend, "getCapabilities", {}, "c-cap")
    capabilities = capabilities_response["result"]; validate(capabilities, schema("BACKEND_CAPABILITIES_V1_2.schema.json"))
    assert CONTRACT_VERSION == "1.2.0"
    assert not (set(capabilities["direct_commands"]) & set(capabilities["transport_deferred_commands"]))
    created = call(backend, "createProject", {"name": "Contract"}, "c-create")["result"]; project_id = created["project_id"]
    assert call(backend, "createProject", {"name": "Ignored duplicate"}, "c-create")["result"] == created
    assert call(backend, "openProject", {"project_id": project_id}, "c-open")["result"]["project_id"] == project_id
    source = pdf(tmp_path / "source.pdf")
    pipeline = {"vision_provider_id": "mock", "text_provider_id": "mock", "source_language": "en", "target_language": "de", "ocr_mode": "auto", "translation_enabled": True, "structure_enabled": False, "output_formats": ["md"]}
    imported_response = call(backend, "importSources", {"project_id": project_id, "paths": [str(source)], "pipeline_config": pipeline}, "c-import")
    imported = imported_response["result"]; batch_id = imported["batch_id"]
    assert call(backend, "importSources", {"project_id": project_id, "paths": [str(source)]}, "c-import")["result"] == imported
    snapshot = call(backend, "getSnapshot", {"project_id": project_id, "batch_id": batch_id}, "c-snapshot")["result"]
    validate(snapshot, schema("BACKEND_SNAPSHOT_V1_2.schema.json")); source_id = snapshot["sources"][0]["source_id"]; job_id = snapshot["jobs"][0]["job_id"]
    assert call(backend, "selectSourceDocument", {"project_id": project_id, "source_id": source_id}, "c-select")["accepted"]
    assert call(backend, "pausePipeline", {"batch_id": batch_id}, "c-pause")["accepted"]
    assert call(backend, "resumePipeline", {"batch_id": batch_id}, "c-resume")["accepted"]
    assert call(backend, "cancelPipeline", {"job_id": job_id}, "c-cancel")["result"]["state"] == "cancelled"
    assert call(backend, "retryFailedStage", {"job_id": job_id}, "c-retry")["result"]["state"] == "queued"
    assert call(backend, "startPipeline", {"batch_id": batch_id}, "c-start")["result"]["counts"] == {"completed": 1}
    assert call(backend, "recoverFromCheckpoint", {"batch_id": batch_id}, "c-recover")["accepted"]
    resource = call(backend, "exportOutputs", {"job_id": job_id}, "c-export")["result"]; validate(resource, schema("DOCUMENT_RESOURCE_RESPONSE_V1_2.schema.json"))
    assert call(backend, "acknowledgeWarning", {"warning_id": "job:test", "project_id": project_id}, "c-ack")["accepted"]
    assert call(backend, "closeProject", {"project_id": project_id}, "c-close")["result"]["state"] == "closed"
    rejected = call(backend, "importSources", {"project_id": project_id, "paths": [str(source)]}, "c-closed-import")
    assert rejected["status"] == "rejected"; validate(rejected["error"], schema("BACKEND_ERROR_V1_2.schema.json"))
    events = backend.events()
    assert events
    for event in events: validate(event, schema("BACKEND_EVENT_V1_2.schema.json"))


def test_rejections_capability_honesty_partial_and_cross_project_link(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    first = call(backend, "createProject", {"name": "First"}, "p1")["result"]
    second = call(backend, "createProject", {"name": "Second"}, "p2")["result"]
    source = pdf(tmp_path / "shared.pdf"); corrupt = tmp_path / "bad.pdf"; corrupt.write_bytes(b"bad")
    partial = call(backend, "importSources", {"project_id": first["project_id"], "paths": [str(source), str(corrupt)]}, "partial")["result"]
    assert partial["imported"] == 1 and partial["failed"] == 1
    linked = call(backend, "importSources", {"project_id": second["project_id"], "paths": [str(source)]}, "linked")["result"]
    assert linked["linked"] == 1 and linked["imported"] == 0
    assert len(backend.snapshot(project_id=first["project_id"])["sources"]) == 1
    assert len(backend.snapshot(project_id=second["project_id"])["sources"]) == 1
    assert len(backend.snapshot(batch_id=linked["batch_id"])["jobs"]) == 1
    missing = backend.execute("createProject", {}, command_id="missing", schema_version="1.2")
    unknown = backend.execute("doesNotExist", {}, command_id="unknown", schema_version="1.2")
    mismatch = backend.execute("getSnapshot", {}, command_id="mismatch", schema_version="1.0")
    direct_without_output = backend.execute(
        "openOutputFolder", {}, command_id="direct-without-output", schema_version="1.2"
    )
    capabilities = backend.capabilities()
    assert "openOutputFolder" in capabilities["direct_commands"]
    assert "openOutputFolder" not in capabilities["transport_deferred_commands"]
    assert all(
        item["status"] == "rejected"
        for item in (missing, unknown, mismatch, direct_without_output)
    )
    assert direct_without_output["error"]["error_code"] == "command_rejected"
