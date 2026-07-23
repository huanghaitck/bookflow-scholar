"""Desktop adapter for the validated Phase 13.5/13.6 production lifecycle."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any, Callable

import fitz
from PIL import Image

from .artifact_resolver import build_artifact_manifest
from .io_utils import atomic_write_json, atomic_write_text, sha256_file
from .multilingual_workspace import (
    build_workspace,
    create_workspace,
    inspect_workspace,
    plan_workspace,
    rebuild_structured_translation_units,
    render_workspace,
    translate_workspace,
)
from .page_quality import analyze_pdf_pages
from .provider_registry import ProviderRegistry, RegistryTranslationProvider
from .providers.mock import MockTranslationProvider
from .publication_structure import (
    NORMALIZER_VERSION as PUBLICATION_NORMALIZER_VERSION,
    SCHEMA_VERSION as PUBLICATION_STRUCTURE_SCHEMA,
    build_deterministic_structure_workspace,
    mechanical_preflight,
    run_structure_workspace,
)


ProgressCallback = Callable[[str, float, dict[str, Any]], None]
StopCallback = Callable[[], str | None]


def _noop_progress(stage: str, progress: float, details: dict[str, Any]) -> None:
    del stage, progress, details


class PipelineControlRequested(RuntimeError):
    def __init__(self, action: str) -> None:
        super().__init__(f"pipeline control requested: {action}")
        self.action = action


class ProductionPipeline:
    """Join Desktop Project/Source/Batch/Job to one generic book workspace."""

    def __init__(self, backend_root: Path, *, provider_config_path: Path | None = None) -> None:
        self.backend_root = backend_root.resolve()
        self.provider_config_path = provider_config_path.resolve() if provider_config_path else None

    def _registry(self) -> ProviderRegistry | None:
        return ProviderRegistry.load(self.provider_config_path) if self.provider_config_path else None

    def _normalize_source(self, job: dict[str, Any], workspace_parent: Path) -> Path:
        source = Path(job["source_path"]).resolve()
        if source.suffix.casefold() == ".pdf":
            return source
        normalized = workspace_parent / "normalized" / f"{job['source_id']}.pdf"
        if normalized.is_file():
            return normalized
        normalized.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            document = fitz.open()
            page = document.new_page(width=width, height=height)
            temporary = normalized.with_suffix(".source.png")
            rgb.save(temporary)
            page.insert_image(page.rect, filename=str(temporary))
            document.save(normalized)
            document.close()
            temporary.unlink(missing_ok=True)
        return normalized

    @staticmethod
    def _control(stop: StopCallback | None) -> None:
        action = stop() if stop else None
        if action:
            raise PipelineControlRequested(action)

    def run(self, job: dict[str, Any], *, progress: ProgressCallback | None = None,
            stop: StopCallback | None = None) -> dict[str, Any]:
        progress = progress or _noop_progress
        config = json.loads(job.get("config_json") or "{}")
        source_language = str(config.get("source_language") or job.get("source_language") or "auto")
        target_language = str(config.get("target_language") or "zh-Hans")
        text_provider_id = str(config.get("text_provider_id") or "mock")
        vision_provider_id = str(config.get("vision_provider_id") or "mock")
        formats = tuple(str(item).lower() for item in (config.get("output_formats") or ["md", "docx", "pdf"])
                        if str(item).lower() in {"md", "docx", "pdf"}) or ("md",)
        workspace_id = str(job.get("workspace_id") or job["source_id"])
        project_id = str(job.get("project_id") or "legacy")
        workspace = self.backend_root / "workspaces" / project_id / workspace_id
        source = self._normalize_source(job, workspace.parent)
        output = self.backend_root / "outputs" / project_id / workspace_id
        registry = self._registry()
        allow_real = bool(registry and registry.allow_real_api and (text_provider_id != "mock" or vision_provider_id != "mock"))
        attempt_ledger_path = self.backend_root / "provider_attempts.jsonl"

        self._control(stop)
        progress("workspace", 0.08, {"workspace": str(workspace)})
        if not (workspace / "bookflow_workspace.json").is_file():
            create_workspace(
                workspace, source, source_language, target_language,
                output_directory=workspace / "rendered", layout_mode="publication",
                workspace_id=workspace_id,
            )

        self._control(stop)
        progress("text_quality", 0.18, {})
        intake_summary_path = workspace / "data/page_intake_summary.json"
        if intake_summary_path.is_file() and (workspace / "data/page_text_quality.jsonl").is_file():
            intake = json.loads(intake_summary_path.read_text("utf-8"))
            quality_records = [json.loads(line) for line in (workspace / "data/page_text_quality.jsonl").read_text("utf-8").splitlines() if line]
            route_records = [json.loads(line) for line in (workspace / "data/ocr_routes.jsonl").read_text("utf-8").splitlines() if line]
            selected_text = {index + 1: str(item.get("text") or "") for index, item in enumerate(route_records)}
        else:
            intake = analyze_pdf_pages(source, workspace / "data", registry=registry,
                                       vision_provider_id=vision_provider_id,
                                       allow_provider_calls=allow_real and vision_provider_id != "mock",
                                       attempt_ledger_path=attempt_ledger_path,
                                       attempt_context={"project_id": project_id, "job_id": str(job["job_id"]),
                                                        "stage": "ocr", "provider_role": "vision"})
            quality_records = intake["quality_records"]
            route_records = intake["route_records"]
            selected_text = intake["selected_text"]

        self._control(stop)
        progress("inspect", 0.28, {"pages": intake["page_count"]})
        inspection_path = workspace / "data/inspection_report.json"
        if not inspection_path.is_file():
            inspection = inspect_workspace(workspace, page_text_overrides=selected_text,
                                           page_quality_records=quality_records,
                                           page_route_records=route_records)
        else:
            inspection = json.loads(inspection_path.read_text("utf-8"))

        self._control(stop)
        progress("structure", 0.4, {})
        structure_path = workspace / "data/book_structure.json"
        existing_structure = json.loads(structure_path.read_text("utf-8")) if structure_path.is_file() else None
        classification_path = workspace / "data/page_classification.jsonl"
        classifications_current = False
        if classification_path.is_file():
            classification_records = [json.loads(line) for line in classification_path.read_text("utf-8").splitlines() if line]
            classifications_current = bool(classification_records) and all(
                item.get("normalizer_version") == PUBLICATION_NORMALIZER_VERSION for item in classification_records)
        if (existing_structure and existing_structure.get("schema_version") == PUBLICATION_STRUCTURE_SCHEMA
                and classifications_current):
            structure = existing_structure
            structure_calls = 0
        else:
            mechanical_preflight(workspace)
            structure = build_deterministic_structure_workspace(workspace)
            if bool(config.get("structure_enabled", True)) and registry and vision_provider_id != "mock":
                deterministic_records = [json.loads(line) for line in
                                         (workspace / "data/page_classification.jsonl").read_text("utf-8").splitlines()
                                         if line]
                selected_pages = sorted(
                    {index + 1 for index, quality in enumerate(quality_records)
                     if (not quality.get("passed")) or
                     "multi_column_reading_order_risk" in quality.get("issue_codes", [])}
                    | {int(item["physical_page"]) for item in deterministic_records
                       if item.get("review_required") or item.get("images") or item.get("tables")}
                )
                if selected_pages:
                    structure = run_structure_workspace(workspace, registry, provider_id=vision_provider_id,
                                                        selected_pages=selected_pages,
                                                        attempt_ledger_path=attempt_ledger_path,
                                                        attempt_context={"project_id": project_id,
                                                                         "job_id": str(job["job_id"]),
                                                                         "stage": "structure",
                                                                         "provider_role": "vision"})
                    structure_calls = int(structure.get("provider_calls_this_run", 0))
                    structure["selected_pages"] = selected_pages
                else:
                    structure_calls = 0
            else:
                structure_calls = 0

        self._control(stop)
        progress("plan", 0.5, {})
        rebuild_structured_translation_units(workspace, page_text_overrides=selected_text)
        plan = plan_workspace(workspace)
        source_actual = json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))["source_language"]
        translation_enabled = bool(config.get("translation_enabled", True)) and source_actual != target_language
        translation: dict[str, Any] = {"api_calls": 0, "pending": 0, "status": "not_requested"}
        provider_name = "none"
        model_alias = None
        if translation_enabled and plan["pending"]:
            self._control(stop)
            progress("translation", 0.62, {"pending_units": plan["pending"]})
            if text_provider_id == "mock":
                provider: Any = MockTranslationProvider()
                provider_name, model_alias = "mock", "mock-v1"
            else:
                if registry is None:
                    raise RuntimeError("real translation requires the configured provider registry")
                profile = registry.get(text_provider_id, "text")
                provider = RegistryTranslationProvider(
                    workspace, registry.client(profile), control=lambda: self._control(stop),
                    attempt_ledger_path=attempt_ledger_path,
                    attempt_context={"project_id": project_id, "job_id": str(job["job_id"]),
                                     "stage": "translation", "provider_role": "language"},
                )
                provider_name, model_alias = profile.provider_id, profile.model
            translation = translate_workspace(workspace, provider, provider_name=provider_name,
                                              model=model_alias or "unknown", batch_size=8,
                                              control=lambda: self._control(stop))
        elif translation_enabled:
            provider_name = text_provider_id

        self._control(stop)
        progress("render", 0.82, {"formats": list(formats)})
        if translation_enabled:
            build = build_workspace(workspace, formats, layout_mode="publication")
            primary_role = "target"
        else:
            rendered = render_workspace(workspace, "source", formats, layout_mode="publication")
            build = {"status": "generated", "roles": {"source": rendered}, "validation": {"valid": True, "errors": []}}
            primary_role = "source"

        self._control(stop)
        progress("validate", 0.94, {})
        output.mkdir(parents=True, exist_ok=True)
        (output / "assets/images").mkdir(parents=True, exist_ok=True)
        (output / "assets/tables").mkdir(parents=True, exist_ok=True)
        (output / "assets/source").mkdir(parents=True, exist_ok=True)
        (output / "checkpoints").mkdir(parents=True, exist_ok=True)
        source_asset_paths: list[tuple[int, Path]] = []
        preview_manifest_path = self.backend_root / "previews" / str(job["source_id"]) / "preview_manifest.json"
        if preview_manifest_path.is_file():
            preview_manifest = json.loads(preview_manifest_path.read_text("utf-8"))
            for item in preview_manifest.get("thumbnails", []):
                source_asset = Path(str(item.get("path") or ""))
                if not source_asset.is_file():
                    continue
                destination = output / "assets/source" / f"page-{int(item['page']):04d}{source_asset.suffix.lower()}"
                shutil.copy2(source_asset, destination)
                source_asset_paths.append((int(item["page"]), destination))
        primary_manifest = build["roles"][primary_role]
        primary_md = primary_manifest.get("outputs", {}).get("md", {}).get("path")
        if primary_md and Path(primary_md).is_file():
            shutil.copy2(primary_md, output / "book.md")
        for role, role_manifest in build["roles"].items():
            for fmt, artifact in role_manifest.get("outputs", {}).items():
                path = Path(artifact["path"]) if isinstance(artifact, dict) and artifact.get("path") else None
                if path and path.is_file():
                    shutil.copy2(path, output / path.name)

        warnings = [{"type": "page_review_required", "page": page, "review_required": True}
                    for page in intake.get("review_pages", [])]
        metadata = {
            "source_id": job["source_id"], "workspace_id": json.loads((workspace / "bookflow_workspace.json").read_text("utf-8"))["workspace_id"],
            "filename": job["filename"], "sha256": job["sha256"], "source_language": source_actual,
            "target_language": target_language, "page_count": int(job["page_count"]),
            "production_pipeline": "phase13.5+validated-phase13.6", "workspace": str(workspace),
        }
        atomic_write_json(output / "metadata.json", metadata)
        atomic_write_json(output / "warnings.json", warnings)
        atomic_write_json(output / "page_intake_summary.json", {key: value for key, value in intake.items()
                                                                  if key not in {"selected_text", "quality_records", "route_records"}})
        atomic_write_json(output / "build_manifest.json", build)
        atomic_write_text(output / "processing_report.md",
                          "# Processing report\n\n"
                          f"- Status: completed\n- Source: {job['filename']}\n"
                          f"- Pipeline: Phase 13.5 generalized engine + validated Phase 13.6 components\n"
                          f"- Pages: {inspection['pages']}\n- Translation units: {inspection['units']}\n"
                          f"- Text quality passed: {intake['quality_passed']}\n"
                          f"- Review pages: {len(intake.get('review_pages', []))}\n"
                          f"- Whole-book DeepSeek calls: 0\n")
        with (output / "source_manifest.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["source_id", "filename", "sha256", "source_path", "workspace_id"])
            writer.writeheader()
            source_row = {**metadata, "source_path": job["source_path"]}
            writer.writerow({name: source_row.get(name, "") for name in writer.fieldnames})
        review = {"schema_version": "human-review-queue-v1", "source_id": job["source_id"],
                  "supported_issue_types": ["ocr_low_confidence", "page_number_anomaly", "heading_level_uncertain",
                    "footnote_ownership_uncertain", "table_failure", "missing_image",
                    "provider_structured_output_failure", "stronger_model_required", "partial_export_failure"],
                  "issues": warnings}
        atomic_write_json(output / "HUMAN_REVIEW_QUEUE.json", review)
        atomic_write_text(output / "HUMAN_REVIEW_QUEUE.md", "# Human review queue\n\n" +
                          ("\n".join(f"- Page {item['page']}: review required" for item in warnings)
                           if warnings else "No pending review items.") + "\n")
        atomic_write_json(output / "checkpoints/job.json", {"job_id": job["job_id"], "status": "completed",
                          "source_sha256": job["sha256"], "workspace": str(workspace)})
        resources = [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
                     for path in sorted(output.rglob("*"))
                     if path.is_file() and path.name != "output_manifest.json"]
        atomic_write_json(output / "output_manifest.json", {"resources": resources})
        artifact_manifest = build_artifact_manifest(
            output_root=output,
            workspace_id=workspace_id,
            project_id=project_id,
            source_id=str(job["source_id"]),
            job_id=str(job["job_id"]),
            source_asset_paths=source_asset_paths,
        )
        progress("completed", 1.0, {"output_path": str(output)})
        return {
            "output_path": str(output), "workspace": str(workspace), "provider_id": provider_name,
            "model_alias": model_alias, "request_count": int(translation.get("api_calls", 0)) + structure_calls,
            "usage": {"calls": int(translation.get("api_calls", 0)) + structure_calls,
                      "whole_book_calls": 0, "glm_page_calls": structure_calls,
                      "deepseek_segment_calls": int(translation.get("api_calls", 0))},
            "warnings": len(warnings), "page_count": inspection["pages"], "unit_count": inspection["units"],
            "quality_passed": intake["quality_passed"], "quality_failed": intake["quality_failed"],
            "review_pending": len(warnings), "build_status": build.get("status"),
            "build_id": artifact_manifest["build_id"],
        }
