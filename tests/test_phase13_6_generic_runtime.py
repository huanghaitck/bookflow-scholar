from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import fitz
import yaml
from typer.testing import CliRunner

from bookflow.manual_review import export_review_package
from bookflow.multilingual_workspace import (
    build_workspace, create_workspace, inspect_workspace, reflow_text, translate_workspace,
)
from bookflow.provider_registry import ProviderRegistry
from bookflow.providers.mock import MockTranslationProvider
from bookflow.publication_structure import run_structure_workspace
from bookflow.renderer_backend import validate_renderer
from bookflow.runtime_cli import app


def _pdf(path: Path, pages: int = 2) -> None:
    doc = fitz.open()
    for number in range(pages):
        page = doc.new_page()
        page.insert_text((50, 70), f"Chapitre {number + 1}\nUne informa-\ntion importante.", fontsize=11)
    doc.save(path); doc.close()


def _config(path: Path) -> None:
    path.write_text(yaml.safe_dump({"allow_real_api": False, "active_translation_provider": "mock",
        "active_vision_provider": "mock", "providers": {"mock": {"provider_type": "mock",
        "model": "test-model", "capabilities": ["text", "vision", "structure", "review"]}}}), "utf-8")


def test_reading_reflow_dehyphenates_and_removes_visual_lines() -> None:
    assert reflow_text("Une informa-\ntion importante.\nLa suite.", "fr") == "Une information importante. La suite."
    assert reflow_text("\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c", "zh-Hans") == "\u7b2c\u4e00\u884c\u7b2c\u4e8c\u884c"


def test_text_docx_has_full_body_width_and_no_hard_break(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; _pdf(source)
    workspace = tmp_path / "workspace"
    create_workspace(workspace, source, "fr", "zh-Hans")
    inspect_workspace(workspace); translate_workspace(workspace, MockTranslationProvider())
    build_workspace(workspace, ("docx",))
    path = next((workspace / "output/source").glob("*.docx"))
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    assert b"<w:br" not in xml
    report = json.loads((workspace / "output/source/render_manifest.json").read_text("utf-8"))
    assert report["page_body_width_inches"] > 6
    assert report["bilingual_layout"] == "stacked"


def test_mock_structure_is_raw_first_append_only_and_terminal_resume_zero(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; _pdf(source)
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "fr", "zh-Hans"); inspect_workspace(workspace)
    config = tmp_path / "providers.yaml"; _config(config); registry = ProviderRegistry.load(config)
    first = run_structure_workspace(workspace, registry)
    assert first["provider_calls_this_run"] == 2
    attempts = (workspace / "logs/structure_attempts.jsonl").read_text("utf-8")
    assert attempts.index('"status": "raw_saved"') < attempts.index('"status": "semantic_unresolved"')
    second = run_structure_workspace(workspace, registry)
    assert second["provider_calls_this_run"] == 0
    assert (workspace / "data/book_structure.json").is_file()
    assert (workspace / "data/page_classification.jsonl").is_file()
    assert (workspace / "data/segmentation_plan.json").is_file()


def test_complete_review_package_contains_required_assets(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; _pdf(source)
    objects = tmp_path / "objects.json"; objects.write_text(json.dumps({"objects": [{
        "object_id": "page-0001-structure", "source_page": 1, "source_file_sha256": "a" * 64,
        "source_text": "Texte", "current_structure": {"columns": 2}, "terminology": ["terme"],
        "place_names": ["Paris"]}]}), "utf-8")
    output = tmp_path / "review"; export_review_package(objects, output, source_pages_pdf=source, complete=True)
    required = {"manifest.json", "instructions.md", "source_pages.pdf", "source_page_images",
                "current_ocr.md", "current_structure.json", "requested_schema.json",
                "manual_patch.template.json", "terminology.csv", "place_names.csv"}
    assert required <= {x.name for x in output.iterdir()}


def test_runtime_cli_exposes_required_grouped_commands(tmp_path: Path) -> None:
    runner = CliRunner()
    root = runner.invoke(app, ["--help"]); assert root.exit_code == 0
    for name in ("inspect", "structure", "plan", "translate", "status", "resume", "render", "validate", "build", "rebuild", "run", "ui", "doctor"):
        assert name in root.stdout
    for group, commands in (("workspace", ("create",)), ("providers", ("list", "validate", "test")),
                            ("review", ("status", "export", "validate", "import"))):
        help_result = runner.invoke(app, [group, "--help"]); assert help_result.exit_code == 0
        for command in commands: assert command in help_result.stdout
    renderer_help = runner.invoke(app, ["renderers", "--help"]); assert renderer_help.exit_code == 0
    for command in ("list", "detect", "validate", "test"): assert command in renderer_help.stdout


def test_provider_public_view_never_contains_credential_value(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PRIVATE_TEST_CREDENTIAL", "do-not-print")
    path = tmp_path / "providers.yaml"; path.write_text(yaml.safe_dump({"allow_real_api": True, "providers": {
        "p": {"provider_type": "openai_compatible", "model": "m", "base_url": "https://example.invalid/v1",
              "api_key_env": "PRIVATE_TEST_CREDENTIAL", "capabilities": ["text"]}}}), "utf-8")
    public = json.dumps(ProviderRegistry.load(path).validate())
    assert "do-not-print" not in public
    assert "PRIVATE_TEST_CREDENTIAL" in public


def test_missing_office_renderer_keeps_md_docx_and_marks_pdf_blocked(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"; _pdf(source)
    workspace = tmp_path / "workspace"; create_workspace(workspace, source, "fr", "zh-Hans")
    inspect_workspace(workspace); translate_workspace(workspace, MockTranslationProvider())
    renderer = tmp_path / "renderer.yaml"; renderer.write_text(yaml.safe_dump({"renderers": {"office": {
        "discovery": "manual", "executable": str(tmp_path / "missing-soffice.exe")}}}), "utf-8")
    result = build_workspace(workspace, pdf_renderer="office", renderer_config=renderer)
    assert result["status"] == "blocked_by_renderer"
    for role in ("source", "target", "bilingual"):
        outputs = result["roles"][role]["outputs"]
        assert outputs["md"]["status"] == "generated"
        assert outputs["docx"]["status"] == "generated"
        assert outputs["pdf"]["status"] == "blocked_by_renderer"


def test_windows_version_resource_recovers_a_hung_version_probe(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "soffice.exe"; executable.touch()
    monkeypatch.setattr("bookflow.renderer_backend.subprocess.run", lambda *args, **kwargs: (
        (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], kwargs.get("timeout")))
    ))
    monkeypatch.setattr("bookflow.renderer_backend._windows_file_version", lambda path: "26.2.4.2")
    result = validate_renderer(executable, timeout_seconds=1)
    assert result["valid"] is True
    assert result["version"] == "26.2.4.2"
    assert result["validation_method"] == "windows_file_version"
