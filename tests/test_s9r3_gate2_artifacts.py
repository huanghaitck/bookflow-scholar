from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import fitz
import pytest

from bookflow.batch_backend import BatchBackend


def _pdf(path: Path, pages: int = 2) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page()
        page.insert_text((50, 70), f"Section {number}\nParagraph for page {number}.")
    document.save(path)
    document.close()
    return path


def test_asset_and_artifact_ids_drive_readback_without_frontend_paths(tmp_path: Path) -> None:
    backend = BatchBackend(tmp_path / "backend")
    project = backend.create_project("Artifact project")
    source = _pdf(tmp_path / "中文 name,with apostrophe's.pdf")
    imported = backend.import_sources(
        project["project_id"], [source], command_id="artifact-import",
        source_languages={str(source): "en"},
        pipeline_config={"source_language": "en", "target_language": "fr"},
    )
    initial = backend.snapshot(project_id=project["project_id"])
    source_row = initial["sources"][0]
    assert "path" not in source_row["thumbnails"][0]
    assert "thumbnail_path" not in source_row["thumbnails"][0]
    assert len(source_row["thumbnails"]) == 2
    for page in source_row["thumbnails"]:
        resolved = backend.resolve_asset(page["asset_id"])
        assert resolved["page_number"] == page["page"]
        assert resolved["data_url"].startswith("data:image/png;base64,")
        assert len(base64.b64decode(resolved["data_url"].split(",", 1)[1])) > 100

    result = backend.run_batch(imported["batch_id"])
    assert result["state"] == "completed"
    snapshot = backend.snapshot(project_id=project["project_id"])
    job = snapshot["jobs"][0]
    manifest = job["pipeline_details"]["artifact_manifest"]
    assert manifest["project_id"] == project["project_id"]
    assert manifest["source_id"] == source_row["source_id"]
    assert manifest["job_id"] == job["job_id"]
    assert manifest["workspace_id"] == job["workspace_id"]
    assert Path(manifest["canonical_output_root"]).resolve() == Path(job["output_path"]).resolve()
    assert len(manifest["source_assets"]) == 2

    for role in ("source_markdown", "target_markdown", "bilingual_markdown"):
        record = manifest[role]
        assert record and "artifact_id" in record and "relative_path" in record
        readback = backend.read_artifact(record["artifact_id"])
        assert readback["content"].strip()
        assert readback["role"] == role
        assert readback["build_id"] == manifest["build_id"]
        assert hashlib.sha256(readback["content"].encode("utf-8")).hexdigest() == readback["sha256"]
        path_result = backend.artifact_path(record["artifact_id"])
        assert Path(path_result["target"]).is_file()
        assert Path(path_result["target"]).resolve().is_relative_to(Path(manifest["canonical_output_root"]).resolve())

    pdf_path = backend.artifact_path(manifest["bilingual_pdf"]["artifact_id"])
    assert pdf_path["mime_type"] == "application/pdf"
    assert pdf_path["page_count"] >= 2
    assert Path(pdf_path["target"]).is_file()
    rendered_page = backend.render_artifact_page(manifest["bilingual_pdf"]["artifact_id"], 2)
    assert rendered_page["page_number"] == 2
    assert rendered_page["page_count"] == pdf_path["page_count"]
    assert rendered_page["data_url"].startswith("data:image/png;base64,")
    assert len(base64.b64decode(rendered_page["data_url"].split(",", 1)[1])) > 1000
    with pytest.raises(ValueError, match="page_number"):
        backend.render_artifact_page(manifest["bilingual_pdf"]["artifact_id"], 0)

    assert {item["role"] for item in snapshot["outputs"]} >= {
        "source_markdown", "target_markdown", "bilingual_markdown",
        "source_docx", "source_pdf", "target_docx", "target_pdf", "bilingual_docx", "bilingual_pdf",
    }

    other = backend.create_project("Other project")
    backend.open_project(other["project_id"])
    with pytest.raises(KeyError):
        backend.read_artifact(manifest["source_markdown"]["artifact_id"])
