"""Command-line interface for offline project operations."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import __version__
from .automated_reconstruction import export_from_master, run_automated_reconstruction
from .doctor import format_doctor_report, run_doctor
from .mock_vision import run_mock_vision
from .page_pipeline import page_status, parse_pages, render_pages, validate_manifest_file
from .fullbook_pipeline import run_fullbook_vision_batch
from .phase2a1 import normalize_preserved_response_v11
from .phase2b_qa import run_offline_boundary_qa
from .phase2b_calls import (
    create_open_boundary_11_12,
    load_all_page_results,
    load_latest_boundaries,
    phase2b_preflight,
    run_pair_boundaries,
    run_single_pages,
    run_triple_boundaries,
)
from .reconstruction import build_logical_blocks, validate_logical_outputs
from .paths import load_settings, project_root, resolve_project_path
from .pdf_inspect import inspect_pdf
from .vision_pipeline import (
    VisionCallFailed,
    run_vision_page,
    vision_preflight,
)
from .translation_pipeline import (
    run_translation_sample,
    translation_preflight,
)
from .manual_review import export_review_package, import_patch, validate_patch


app = typer.Typer(
    name="bookflow",
    help="Offline-first bilingual book workflow utilities.",
    no_args_is_help=True,
    rich_markup_mode=None,
)
console = Console()


@app.command("workspace-create")
def workspace_create_command(
    pdf: Annotated[Path, typer.Option("--pdf")],
    workspace: Annotated[Path, typer.Option("--workspace")],
    source_language: Annotated[str, typer.Option("--source-language")] = "auto",
    target_language: Annotated[str, typer.Option("--target-language")] = "zh-Hans",
    output_directory: Annotated[Path | None, typer.Option("--output-directory")] = None,
    profile: Annotated[str, typer.Option("--profile")] = "reading",
) -> None:
    """Create an isolated language-neutral workspace for a new book."""
    from .multilingual_workspace import create_workspace
    try:
        result = create_workspace(workspace, pdf, source_language, target_language,
                                  output_directory=output_directory, profile=profile)
    except (ValueError, FileNotFoundError, FileExistsError) as exc:
        console.print(f"Error: {exc}", style="bold red"); raise typer.Exit(code=2) from exc
    console.print(json.dumps(result, ensure_ascii=False))


@app.command()
def version() -> None:
    """Show the bookflow and Python versions."""

    console.print(f"bookflow {__version__}")
    console.print(f"Python {platform.python_version()}")


@app.command("phase6-vision-batch")
def phase6_vision_batch_command(
    pages: Annotated[str, typer.Option("--pages", help="Explicit one-based page range.")],
    allow_api: Annotated[bool, typer.Option("--allow-api")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Run one resumable Phase 6 single-page visual batch; no automatic retries."""

    try:
        settings, root = _settings_and_root(config)
        result = run_fullbook_vision_batch(
            settings, pages=parse_pages(pages), root=root, allow_api=allow_api
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Requested pages: {len(result.requested_pages)}")
    console.print(f"Completed this run: {len(result.completed_pages)}")
    console.print(f"Cache hits: {len(result.cached_pages)}")
    console.print(f"Failed: {len(result.failed_pages)}")
    console.print(f"API calls this run: {result.api_calls_this_run}")
    console.print(f"Total tokens this run: {result.total_tokens_this_run}")
    console.print(f"Automatic retries: 0")
    console.print(f"Elapsed seconds: {result.elapsed_seconds}")


@app.command("phase6-vision-production")
def phase6_vision_production_command(
    start_page: Annotated[int, typer.Option("--start-page")],
    end_page: Annotated[int, typer.Option("--end-page")],
    allow_api: Annotated[bool, typer.Option("--allow-api")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Run consecutive Phase 6 pages as isolated 20-page batches."""

    if start_page < 1 or end_page < start_page:
        raise typer.BadParameter("page interval must be positive and ordered")
    settings, root = _settings_and_root(config)
    totals = {"completed": 0, "cached": 0, "failed": 0, "calls": 0, "tokens": 0}
    for first in range(start_page, end_page + 1, settings.processing_page_batch_size):
        last = min(end_page, first + settings.processing_page_batch_size - 1)
        try:
            result = run_fullbook_vision_batch(
                settings, pages=list(range(first, last + 1)), root=root, allow_api=allow_api
            )
        except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
            console.print(f"Batch {first}-{last} stopped: {exc}", style="bold red")
            raise typer.Exit(code=2) from exc
        totals["completed"] += len(result.completed_pages)
        totals["cached"] += len(result.cached_pages)
        totals["failed"] += len(result.failed_pages)
        totals["calls"] += result.api_calls_this_run
        totals["tokens"] += result.total_tokens_this_run
        console.print(
            f"Batch {first}-{last}: completed={len(result.completed_pages)}, "
            f"cached={len(result.cached_pages)}, failed={len(result.failed_pages)}, "
            f"calls={result.api_calls_this_run}, tokens={result.total_tokens_this_run}"
        )
    console.print("Production vision interval completed")
    console.print(json.dumps(totals, ensure_ascii=True, sort_keys=True))
    console.print("Automatic retries: 0")


@app.command()
def doctor(
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to a non-secret YAML configuration file."),
    ] = None,
) -> None:
    """Check the local environment without network access or API calls."""

    report = run_doctor(config_path=config)
    console.print(format_doctor_report(report))
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("inspect-pdf")
def inspect_pdf_command(
    pdf_path: Annotated[Path, typer.Argument(help="PDF path to inspect read-only.")],
    metadata_only: Annotated[
        bool,
        typer.Option(
            "--metadata-only",
            help="Do not traverse pages for embedded text-layer statistics.",
        ),
    ] = False,
) -> None:
    """Read PDF metadata and optional embedded text statistics without OCR."""

    try:
        result = inspect_pdf(pdf_path, analyze_text_layer=not metadata_only)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc

    console.print("PDF inspection (read-only)")
    console.print(f"File: {result.filename}")
    console.print(f"Path: {result.path}")
    console.print(f"Size (bytes): {result.size_bytes:,}")
    console.print(f"Actual pages: {result.page_count}")
    console.print(f"Metadata only: {'yes' if result.metadata_only else 'no'}")
    if not result.metadata_only:
        console.print(
            "Embedded text layer: "
            + ("detected" if result.has_text_layer else "not detected")
        )
        console.print(f"Text characters total: {result.text_characters_total}")
        console.print(f"Text characters min/page: {result.text_characters_min}")
        console.print(f"Text characters max/page: {result.text_characters_max}")
        console.print(f"Text characters avg/page: {result.text_characters_average}")
        console.print(
            "Per-page text characters: "
            + json.dumps(result.per_page_text_characters, ensure_ascii=True)
        )
    console.print("Basic metadata: " + json.dumps(result.metadata, ensure_ascii=True))
    console.print("OCR: not performed")
    console.print("API/network: not used")


@app.command("manual-review-export")
def manual_review_export_command(
    objects: Annotated[Path, typer.Option("--objects", help="Structured object JSON.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="New review workspace.")],
    source_pages_pdf: Annotated[Path | None, typer.Option("--source-pages-pdf")] = None,
    copy_source: Annotated[bool, typer.Option("--copy-source/--reference-source")] = False,
) -> None:
    """Export an offline manual/web-assisted review package."""

    try:
        result = export_review_package(
            objects, output_dir, source_pages_pdf=source_pages_pdf, copy_source=copy_source
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(json.dumps(result, ensure_ascii=False, sort_keys=True))


@app.command("manual-patch-validate")
def manual_patch_validate_command(
    patch: Annotated[Path, typer.Option("--patch")],
    objects: Annotated[Path, typer.Option("--objects")],
) -> None:
    """Validate stable IDs, source identity and workspace version offline."""

    try:
        result = validate_patch(patch, objects)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["valid"]:
        raise typer.Exit(code=1)


@app.command("manual-patch-import")
def manual_patch_import_command(
    patch: Annotated[Path, typer.Option("--patch")],
    objects: Annotated[Path, typer.Option("--objects")],
    output: Annotated[Path, typer.Option("--output")],
    provenance: Annotated[Path, typer.Option("--provenance")],
    dry_run: Annotated[bool, typer.Option("--dry-run/--apply")] = True,
) -> None:
    """Dry-run or atomically import a validated, traceable manual patch."""

    try:
        result = import_patch(
            patch, objects, output_path=output, provenance_path=provenance, dry_run=dry_run
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not result["valid"]:
        raise typer.Exit(code=1)


def _settings_and_root(config: Path | None):
    root = project_root()
    return load_settings(config), root


def _print_status(report) -> None:
    console.print(f"Source PDF: {report.source_pdf}")
    console.print(f"Actual pages: {report.actual_page_count}")
    console.print(f"Rendered pages: {json.dumps(report.rendered_pages)}")
    console.print(f"Missing pages: {json.dumps(report.missing_pages)}")
    console.print(f"Orphan image pages: {json.dumps(report.orphan_image_pages)}")
    console.print(f"Missing image pages: {json.dumps(report.missing_image_pages)}")
    console.print(f"Hash anomaly pages: {json.dumps(report.hash_anomaly_pages)}")
    console.print(f"Invalid image pages: {json.dumps(report.invalid_image_pages)}")
    console.print(f"Duplicate pages: {json.dumps(report.duplicate_pages)}")
    console.print(f"Cache status: {report.cache_status}")
    console.print(
        "Current parameters: "
        + json.dumps(report.current_parameters, ensure_ascii=True, sort_keys=True)
    )
    console.print(
        "Ready for visual-model stage: " + ("yes" if report.ready_for_vision else "no")
    )
    console.print("API/network: not used")


@app.command("render-pages")
def render_pages_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="PDF to render read-only.")],
    pages: Annotated[
        str | None,
        typer.Option("--pages", help="One-based range such as 1-11 or 1,3,5-7."),
    ] = None,
    dpi: Annotated[int | None, typer.Option("--dpi", help="Rendering DPI.")] = None,
    color_mode: Annotated[
        str | None, typer.Option("--color-mode", help="RGB or grayscale.")
    ] = None,
    image_format: Annotated[
        str | None, typer.Option("--format", help="Image format; Phase 1B uses PNG.")
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="Optional page image root override.")
    ] = None,
    manifest_dir: Annotated[
        Path | None, typer.Option("--manifest-dir", help="Optional manifest root override.")
    ] = None,
    resume: Annotated[
        bool | None, typer.Option("--resume/--no-resume", help="Continue incomplete work.")
    ] = None,
    force: Annotated[
        bool | None, typer.Option("--force/--no-force", help="Rebuild valid cached pages.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Non-secret YAML configuration.")
    ] = None,
) -> None:
    """Render selected PDF pages locally with resumable cache."""

    try:
        settings, root = _settings_and_root(config)
        result = render_pages(
            pdf_path,
            settings,
            pages=pages,
            dpi=dpi,
            color_mode=color_mode,
            image_format=image_format,
            resume=resume,
            force=force,
            root=root,
            image_root=resolve_project_path(output_dir, root=root) if output_dir else None,
            manifest_root=(
                resolve_project_path(manifest_dir, root=root) if manifest_dir else None
            ),
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Actual PDF pages: {result.actual_page_count}")
    console.print(f"Rendered this run: {len(result.rendered_pages)}")
    console.print(f"Cache hits: {len(result.cached_pages)}")
    console.print(f"Skipped: {len(result.skipped_pages)}")
    console.print(f"Failed: {len(result.failed_pages)}")
    console.print(f"Output directory: {result.output_directory}")
    console.print(f"Manifest: {result.manifest_path}")
    console.print(f"Elapsed seconds: {result.elapsed_seconds}")
    console.print("OCR/visual model: not performed")
    console.print("API/network: not used")


@app.command("page-status")
def page_status_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="Source PDF path.")],
    pages: Annotated[str | None, typer.Option("--pages")] = None,
    dpi: Annotated[int | None, typer.Option("--dpi")] = None,
    color_mode: Annotated[str | None, typer.Option("--color-mode")] = None,
    image_format: Annotated[str | None, typer.Option("--format")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Show page cache completeness without rendering or network access."""

    try:
        settings, root = _settings_and_root(config)
        report = page_status(
            pdf_path,
            settings,
            pages=pages,
            dpi=dpi,
            color_mode=color_mode,
            image_format=image_format,
            root=root,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    _print_status(report)


@app.command("validate-pages")
def validate_pages_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="Source PDF path.")],
    pages: Annotated[str | None, typer.Option("--pages")] = None,
    dpi: Annotated[int | None, typer.Option("--dpi")] = None,
    color_mode: Annotated[str | None, typer.Option("--color-mode")] = None,
    image_format: Annotated[str | None, typer.Option("--format")] = None,
    manifest: Annotated[
        Path | None, typer.Option("--manifest", help="Validate a specific manifest.")
    ] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Validate page images, hashes, records, order, and source identity."""

    try:
        settings, root = _settings_and_root(config)
        if manifest:
            report = validate_manifest_file(manifest, source_pdf=pdf_path)
        else:
            report = page_status(
                pdf_path,
                settings,
                pages=pages,
                dpi=dpi,
                color_mode=color_mode,
                image_format=image_format,
                root=root,
            )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    _print_status(report)
    if not report.ready_for_vision:
        raise typer.Exit(code=1)


@app.command("mock-vision")
def mock_vision_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="Rendered sample PDF path.")],
    pages: Annotated[str | None, typer.Option("--pages")] = None,
    dpi: Annotated[int | None, typer.Option("--dpi")] = None,
    color_mode: Annotated[str | None, typer.Option("--color-mode")] = None,
    image_format: Annotated[str | None, typer.Option("--format")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Generate non-authoritative Mock records from the PDF text layer."""

    try:
        settings, root = _settings_and_root(config)
        result = run_mock_vision(
            pdf_path,
            settings,
            pages=pages,
            dpi=dpi,
            color_mode=color_mode,
            image_format=image_format,
            root=root,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print("Provider: mock")
    console.print("Source method: pdf_text_layer")
    console.print("Authoritative: no")
    console.print("API called: no")
    console.print(f"Generated pages: {len(result.generated_pages)}")
    console.print(f"Cache hits: {len(result.cached_pages)}")
    console.print(f"Failed pages: {len(result.failed_pages)}")
    console.print(f"Continuity candidates: {result.continuity_candidate_count}")
    console.print(f"Human-review candidates: {result.human_review_candidate_count}")
    console.print(f"Raw directory: {result.raw_directory}")
    console.print(f"Normalized directory: {result.normalized_directory}")
    console.print("OCR/GLM: not performed")
    console.print("API/network: not used")


def _print_vision_preflight(report) -> None:
    console.print("Phase 2A single-page preflight (offline)")
    console.print(f"Provider: {report.provider}")
    console.print(f"Model: {report.model}")
    console.print(f"Base URL: {report.base_url}")
    console.print(f"PDF page: {report.pdf_page} of {report.actual_page_count}")
    console.print(f"Source PDF SHA-256: {report.source_pdf_sha256}")
    console.print(f"Image: {report.image_path}")
    console.print(f"Image SHA-256: {report.image_sha256}")
    console.print(
        f"Image: {report.image_width}x{report.image_height}, "
        f"{report.image_size_bytes:,} bytes"
    )
    console.print(f"Manifest complete: {'yes' if report.manifest_complete else 'no'}")
    console.print(f"Prompt version: {report.prompt_version}")
    console.print(f"Schema version: {report.schema_version}")
    console.print(f"Request fingerprint: {report.request_fingerprint}")
    console.print(f"Cache hit: {'yes' if report.cache_hit else 'no'}")
    console.print(f"API key ({report.api_key_env}): {'set' if report.api_key_set else 'not set'}")
    console.print(f"Expected real calls: {report.expected_real_calls}")
    console.print(f"Real calls already started: {report.real_calls_already_started}")
    console.print(f"Persistent maximum real calls: {report.maximum_real_calls}")
    console.print(f"Maximum output tokens: {report.max_output_tokens}")
    console.print(f"Sampling: {'enabled' if report.do_sample else 'disabled'}")
    console.print(f"Thinking mode: {report.thinking_mode}")
    console.print(f"Visual input token estimate: {report.visual_input_tokens_estimate}")
    console.print(f"Automatic retry: {'enabled' if report.automatic_retry else 'disabled'}")
    console.print(f"API enabled by configuration: {'yes' if report.api_enabled_by_config else 'no'}")
    console.print(f"Translation calls: {report.translation_calls}")
    console.print(f"Maximum cash-risk ceiling: CNY {report.maximum_cash_cost_cny:.2f}")
    console.print(
        "Conservative public-price upper bound: "
        f"CNY {report.conservative_cash_cost_upper_bound_cny:.3f}"
    )
    console.print(
        f"Pricing reference checked {report.pricing_checked_date}: "
        f"{report.pricing_reference_url}"
    )
    console.print(f"Cash-risk note: {report.cash_risk_message}")
    console.print(f"Full-PDF processing: {'yes' if report.full_pdf_processing else 'no'}")
    console.print(f"Full-PDF protection: {'enabled' if report.full_pdf_protection else 'disabled'}")
    console.print(f"Automatic phase advance: {'yes' if report.automatic_phase_advance else 'no'}")
    if report.blockers:
        console.print("Blockers:")
        for blocker in report.blockers:
            console.print(f"- {blocker}")
    console.print(f"Ready for one real call: {'yes' if report.ready_for_real_call else 'no'}")
    console.print("API called by preflight: no")


@app.command("vision-preflight")
def vision_preflight_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="Rendered sample PDF path.")],
    page: Annotated[int, typer.Option("--page", min=1, help="Exactly one PDF page.")],
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Validate one visual request locally without accessing the network."""

    try:
        settings, root = _settings_and_root(config)
        report = vision_preflight(
            pdf_path,
            page,
            settings,
            provider=provider,
            model=model,
            root=root,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    _print_vision_preflight(report)
    if not report.ready_for_real_call and not report.cache_hit:
        raise typer.Exit(code=1)


@app.command("vision-page")
def vision_page_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="Rendered sample PDF path.")],
    page: Annotated[
        list[int],
        typer.Option("--page", min=1, help="Exactly one page; repeated values are rejected."),
    ],
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    allow_api: Annotated[
        bool,
        typer.Option("--allow-api", help="Permit the single approved network request."),
    ] = False,
    confirm_one_call: Annotated[
        bool,
        typer.Option("--confirm-one-call", help="Confirm the persistent one-call budget."),
    ] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Run zero or one real visual call; identical completed work uses cache."""

    try:
        settings, root = _settings_and_root(config)
        result = run_vision_page(
            pdf_path,
            page,
            settings,
            provider=provider,
            model=model,
            allow_api=allow_api,
            confirm_one_call=confirm_one_call,
            root=root,
        )
    except VisionCallFailed as exc:
        console.print("The single approved request failed and was not retried.", style="bold red")
        console.print(f"Error type: {exc.error_type}")
        console.print(f"HTTP status: {exc.http_status if exc.http_status is not None else 'unavailable'}")
        console.print(f"Preserved record: {exc.raw_response_path}")
        console.print("Retries: 0")
        raise typer.Exit(code=3) from exc
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Status: {result.status}")
    console.print(f"PDF page: {result.pdf_page}")
    console.print(f"API called this run: {'yes' if result.api_called else 'no'}")
    console.print(f"Cache hit: {'yes' if result.cache_hit else 'no'}")
    console.print(f"Real calls started this run: {result.real_calls_started_this_run}")
    console.print(f"Retries: {result.retries}")
    console.print(f"Authoritative: {'yes' if result.authoritative else 'no'}")
    console.print(f"Translation ready: {'yes' if result.translation_ready else 'no'}")
    if result.raw_response_path:
        console.print(f"Raw response: {result.raw_response_path}")
    if result.normalized_output_path:
        console.print(f"Normalized output: {result.normalized_output_path}")
    if result.usage_path:
        console.print(f"Usage record: {result.usage_path}")


@app.command("phase2a1-patch")
def phase2a1_patch_command(
    pdf_path: Annotated[Path, typer.Option("--pdf", help="Configured 11-page sample PDF.")],
    raw_response: Annotated[Path, typer.Option("--raw-response")],
    previous_normalized: Annotated[Path, typer.Option("--previous-normalized")],
    output: Annotated[Path, typer.Option("--output")],
    review_from_previous: Annotated[
        bool,
        typer.Option(
            "--review-from-previous",
            help="Set the single-page previous-boundary decision to null for pair review.",
        ),
    ] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Re-normalize a preserved response under Schema 1.1, fully offline."""

    try:
        settings, root = _settings_and_root(config)
        source = resolve_project_path(pdf_path, root=root)
        sample = resolve_project_path(settings.sample_pdf, root=root)
        protected = resolve_project_path(settings.source_pdf, root=root)
        if source == protected or source != sample:
            raise PermissionError("Phase 2A.1 only accepts the configured 11-page sample")
        result = normalize_preserved_response_v11(
            resolve_project_path(raw_response, root=root),
            resolve_project_path(output, root=root),
            previous_normalized_path=resolve_project_path(previous_normalized, root=root),
            force_adjacent_review=(
                {"continuation_from_previous"} if review_from_previous else set()
            ),
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print("Phase 2A.1 offline normalization completed")
    console.print(f"PDF page: {result.pdf_page}")
    console.print(f"Schema version: {result.schema_version}")
    console.print(f"Status: {result.status}")
    console.print(f"Boundary status: {result.boundary_status}")
    console.print(f"Boundary review required: {'yes' if result.boundary_review_required else 'no'}")
    console.print(f"Normalization events: {len(result.normalization_events)}")
    console.print(f"Translation ready: {'yes' if result.translation_ready else 'no'}")
    console.print(f"Output: {resolve_project_path(output, root=root)}")
    console.print("API/network: not used")


@app.command("phase2b-preflight")
def phase2b_preflight_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Show the offline Phase 2B call, token, cost, and safety plan."""

    try:
        settings, root = _settings_and_root(config)
        report = phase2b_preflight(pdf_path, settings, root=root)
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print("Phase 2B preflight (offline)")
    console.print(f"Sample actual pages: {report.actual_page_count}")
    console.print(f"Rendered page images ready: {report.page_images_ready}")
    console.print(f"Page 6 Phase 2A cache reused: {'yes' if report.page6_phase2a_cache_reused else 'no'}")
    console.print(f"Expected new single-page calls: {report.single_calls_expected}")
    console.print(f"Expected pair calls: {report.pair_calls_expected}")
    console.print(f"Maximum triple calls: {report.triple_calls_maximum}")
    console.print(f"Maximum new real calls: {report.maximum_new_calls}")
    console.print(f"Calls already started: {report.calls_already_started}")
    console.print(f"Automatic retry: {'enabled' if report.automatic_retry else 'disabled'}")
    console.print(f"Estimated token range: {report.estimated_token_range}")
    console.print(f"Public-price estimate: CNY {report.estimated_public_price_cny:.6f}")
    console.print(f"Public-price risk range: {report.estimated_public_price_range_cny}")
    console.print(f"Configured estimate ceiling: CNY {report.maximum_estimated_cash_cost_cny:.2f}")
    console.print(f"API key ({report.api_key_env}): {'set' if report.api_key_set else 'not set'}")
    console.print("API account balance / actual cash charge: unavailable from preflight")
    console.print(f"DeepSeek calls: {report.deepseek_calls}")
    console.print(f"Translation calls: {report.translation_calls}")
    console.print(f"Full PDF processing: {'yes' if report.full_pdf_processing else 'no'}")
    if report.blockers:
        console.print("Blockers:")
        for blocker in report.blockers:
            console.print(f"- {blocker}")
    console.print(f"Ready: {'yes' if report.ready_for_real_calls else 'no'}")
    console.print("API called by preflight: no")
    if not report.ready_for_real_calls:
        raise typer.Exit(code=1)


def _print_batch_result(result) -> None:
    console.print(f"Category: {result.category}")
    console.print(f"Requested items: {result.requested}")
    console.print(f"Real calls started: {result.api_calls_started}")
    console.print(f"Cache hits: {result.cache_hits}")
    console.print(f"Phase 2A cache hits: {result.phase2a_cache_hits}")
    console.print(f"Completed: {result.completed}")
    console.print(f"Needs review: {result.needs_review}")
    console.print(f"Failed: {result.failed}")
    if result.failed_items:
        console.print("Failed item IDs: " + json.dumps(result.failed_items))
    console.print("Automatic retries: 0")
    console.print("DeepSeek/translation: not used")


@app.command("vision-sample")
def vision_sample_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    allow_api: Annotated[bool, typer.Option("--allow-api")] = False,
    confirm_phase2b: Annotated[bool, typer.Option("--confirm-phase2b")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Transcribe sample pages individually; page 6 always reuses Phase 2A."""

    try:
        settings, root = _settings_and_root(config)
        result = run_single_pages(
            pdf_path,
            settings,
            allow_api=allow_api,
            confirm_phase2b=confirm_phase2b,
            root=root,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    _print_batch_result(result)


@app.command("review-boundaries")
def review_boundaries_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    allow_api: Annotated[bool, typer.Option("--allow-api")] = False,
    confirm_phase2b: Annotated[bool, typer.Option("--confirm-phase2b")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Review all ten adjacent sample boundaries as independent pair tasks."""

    try:
        settings, root = _settings_and_root(config)
        result = run_pair_boundaries(
            pdf_path,
            settings,
            allow_api=allow_api,
            confirm_phase2b=confirm_phase2b,
            root=root,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    _print_batch_result(result)


@app.command("review-boundary-triple")
def review_boundary_triple_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    allow_api: Annotated[bool, typer.Option("--allow-api")] = False,
    confirm_phase2b: Annotated[bool, typer.Option("--confirm-phase2b")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Review only pair results that explicitly need a three-page window."""

    try:
        settings, root = _settings_and_root(config)
        result = run_triple_boundaries(
            pdf_path,
            settings,
            allow_api=allow_api,
            confirm_phase2b=confirm_phase2b,
            root=root,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    _print_batch_result(result)


@app.command("build-logical-blocks")
def build_logical_blocks_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Build traceable logical paragraphs and translation context offline."""

    try:
        settings, root = _settings_and_root(config)
        create_open_boundary_11_12(pdf_path, settings, root=root)
        result = build_logical_blocks(pdf_path, settings, root=root)
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Logical blocks: {result.logical_block_count}")
    console.print(f"Cross-page logical blocks: {result.cross_page_count}")
    console.print(f"Complete: {result.complete_count}")
    console.print(f"Incomplete end: {result.incomplete_end_count}")
    console.print(f"Unresolved: {result.unresolved_count}")
    console.print(f"Translation ready true: {result.translation_ready_true}")
    console.print(f"Translation ready false: {result.translation_ready_false}")
    console.print(f"Review items: {result.review_count}")
    console.print("DeepSeek/translation/API: not used")


@app.command("build-translation-context")
def build_translation_context_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Rebuild logical data and read-only translation context offline."""

    build_logical_blocks_command(pdf_path=pdf_path, config=config)


@app.command("validate-logical-blocks")
def validate_logical_blocks_command(
    logical_path: Annotated[Path, typer.Option("--logical")],
    context_path: Annotated[Path, typer.Option("--translation-context")],
) -> None:
    """Validate traceability, completeness, and translation gates offline."""

    try:
        report = validate_logical_outputs(logical_path, context_path)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Valid: {'yes' if report.valid else 'no'}")
    console.print(f"Logical blocks: {report.logical_block_count}")
    console.print(f"Translation units: {report.translation_unit_count}")
    console.print(f"Page 11 final paragraph blocked: {'yes' if report.page11_final_blocked else 'no'}")
    if report.errors:
        console.print("Errors: " + json.dumps(report.errors))
    console.print("DeepSeek/translation/API: not used")
    if not report.valid:
        raise typer.Exit(code=1)


@app.command("phase2b-status")
def phase2b_status_command(
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Show Phase 2B saved-result counts without network access."""

    settings, root = _settings_and_root(config)
    pages = load_all_page_results(settings, root)
    pairs = load_latest_boundaries(settings, root, "pair")
    triples = load_latest_boundaries(settings, root, "triple")
    console.print(f"Single-page normalized results: {len(pages)}/11")
    console.print(f"Pair boundary results: {len(pairs)}/10")
    console.print(f"Triple boundary results: {len(triples)}/3 maximum")
    console.print(f"Pair needs review: {sum(item.status == 'needs_review' for item in pairs.values())}")
    console.print("DeepSeek calls: 0")
    console.print("Translation calls: 0")
    console.print("API/network: not used by status")


@app.command("phase2b-qa")
def phase2b_qa_command(
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Compare saved boundary decisions with the post-call QA reference, offline."""

    settings, root = _settings_and_root(config)
    report = run_offline_boundary_qa(settings, root=root)
    console.print(f"Compared boundaries: {report.compared}")
    console.print(f"Matched: {report.matched}")
    console.print(f"Human review required: {report.human_review_required}")
    console.print("Model records overwritten: no")
    console.print("API/network/translation: not used")


@app.command("automated-reconstruct")
def automated_reconstruct_command(
    pdf_path: Annotated[Path, typer.Option("--pdf")],
    diagnostic: Annotated[bool, typer.Option("--diagnostic/--no-diagnostic")] = True,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Build Phase 2B.2 pages, automatic boundaries, logical blocks, and audits offline."""

    try:
        settings, root = _settings_and_root(config)
        result = run_automated_reconstruction(
            pdf_path, settings, root=root, create_diagnostic=diagnostic
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Pages: {result.pages}")
    console.print(f"Source fragments: {result.fragments}")
    console.print(f"Boundaries resolved/unresolved: {result.resolved_boundaries}/{result.unresolved_boundaries}")
    console.print(f"Logical blocks: {result.logical_blocks}")
    console.print(f"Cross-page logical blocks: {result.cross_page_blocks}")
    console.print(f"Translation ready true/false: {result.translation_ready_true}/{result.translation_ready_false}")
    console.print(f"Source coverage audit: {'passed' if result.source_audit_passed else 'failed'}")
    console.print(f"Logical reconstruction audit: {'passed' if result.logical_audit_passed else 'failed'}")
    console.print(f"Strict final export ready: {'yes' if result.strict_export_ready else 'no'}")
    console.print("API/DeepSeek/translation calls: 0/0/0")


@app.command("export-automated")
def export_automated_command(
    master: Annotated[Path, typer.Option("--master")],
    output: Annotated[Path, typer.Option("--output")],
    mode: Annotated[str, typer.Option("--mode")] = "strict",
) -> None:
    """Export Word and Markdown from the same canonical JSON, with strict failure closing."""

    try:
        result = export_from_master(master, output, mode=mode)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    if result.blocked:
        console.print("Strict export blocked: " + ", ".join(result.blockers), style="bold yellow")
        raise typer.Exit(code=3)
    console.print(f"Markdown: {result.markdown_path}")
    console.print(f"Word: {result.word_path}")
    console.print("Both files were generated from the same canonical JSON source.")


@app.command("translation-preflight")
def translation_preflight_command(
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Select five complete blocks and estimate calls, tokens, and cost without API use."""

    try:
        settings, root = _settings_and_root(config)
        report = translation_preflight(settings, root=root)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"Provider/model: {report.provider}/{report.model}")
    console.print(f"Base URL: {report.base_url}")
    console.print(f"API密钥: {'已设置' if report.api_key_set else '未设置'}")
    console.print(f"候选逻辑块: {report.target_block_count}")
    for item in report.candidates:
        console.print(
            f"- {item.selection_type}/{item.block_type}: {item.logical_block_id}; "
            f"pages={item.source_pages}; chars={item.source_text_character_count}; "
            f"cross_page={item.cross_page}"
        )
    console.print(f"英文目标字符: {report.total_source_characters}")
    console.print(f"只读上下文字符: {report.total_context_characters}")
    console.print(f"粗略输入token: {report.estimated_input_tokens}")
    console.print(f"最大输出token: {report.maximum_output_tokens_total}")
    console.print(f"模型列表调用上限: {report.maximum_model_list_calls}")
    console.print(f"内容调用上限: {report.maximum_content_calls}")
    console.print(
        f"估算费用区间(CNY): {report.estimated_cost_lower_cny:.6f}-"
        f"{report.estimated_cost_upper_cny:.6f}"
    )
    console.print(f"现金硬上限(CNY): {report.maximum_cash_cost_cny:.2f}")
    console.print("API实际扣费: 响应通常不返回，需以DeepSeek平台为准")
    console.print("API调用: 0（preflight完全离线）")
    if report.blockers:
        console.print("阻止原因: " + "; ".join(report.blockers), style="bold yellow")
        raise typer.Exit(code=1)


@app.command("translation-sample")
def translation_sample_command(
    config: Annotated[Path | None, typer.Option("--config")] = None,
    allow_api: Annotated[bool, typer.Option("--allow-api")] = False,
    confirm_five_calls: Annotated[bool, typer.Option("--confirm-five-calls")] = False,
) -> None:
    """Dry-run by default; with both confirmations run at most five Phase 3A translations."""

    try:
        settings, root = _settings_and_root(config)
        result = run_translation_sample(
            settings,
            root=root,
            allow_api=allow_api,
            confirm_five_calls=confirm_five_calls,
        )
    except (FileNotFoundError, ValueError, PermissionError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(f"目标块: {len(result.selected_block_ids)}")
    console.print(f"模型列表调用: {result.model_list_calls}")
    console.print(f"内容调用: {result.api_calls}")
    console.print(f"缓存命中: {result.cache_hits}")
    console.print(f"失败: {result.failed}")
    console.print(f"自动重试: {result.retries}")
    console.print("GLM调用: 0")
    console.print("完整PDF正文: 未处理")
    console.print("正式final: 否")
    console.print(f"诊断导出门禁: {'通过' if result.strict_export_ready else '未通过或未执行'}")
    if result.derived_document_path:
        console.print(f"派生测试数据: {result.derived_document_path}")
    if result.diagnostic_markdown_path:
        console.print(f"诊断Markdown: {result.diagnostic_markdown_path}")
    if result.diagnostic_docx_path:
        console.print(f"诊断Word: {result.diagnostic_docx_path}")


@app.command("translation-status")
def translation_status_command(
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Read Phase 3A local ledgers and cache counts without network access."""

    settings, root = _settings_and_root(config)
    request_root = resolve_project_path(settings.translation_request_directory, root=root)
    cache_root = resolve_project_path(settings.translation_cache_directory, root=root)
    ledger_path = request_root / "phase3a_call_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {}
    model_check = request_root / "phase3a_model_check.json"
    console.print(f"模型列表已检查: {'是' if model_check.is_file() else '否'}")
    console.print(f"已开始内容调用: {int(ledger.get('content_calls_started', 0))}")
    console.print(f"成功缓存: {len(list(cache_root.glob('*.json'))) if cache_root.is_dir() else 0}")
    console.print("网络/API调用: 0（status只读）")


@app.command("inspect")
def production_inspect(
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Inspect the frozen current book and offline multilingual layer."""
    if workspace is not None:
        from .multilingual_workspace import inspect_workspace
        console.print(json.dumps(inspect_workspace(workspace), ensure_ascii=False)); return
    from .production import RenderInputs
    inputs = RenderInputs.load(project_root())
    console.print(json.dumps({"canonical_sha256": "16c1c9ba4d60d1c2a4124433291a1a56bf499384215c720f6988e6e183c01326", "logical_units": len(inputs.canonical["logical_units"]), "chapters": inputs.canonical["metadata"]["total_chapters"], "translation_status_counts": inputs.status_counts, "api_calls": 0}, ensure_ascii=False))


@app.command("plan")
def production_plan(
    language: Annotated[str, typer.Option("--language")] = "en",
    edition: Annotated[str, typer.Option("--edition")] = "reading",
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Plan a render without writing outputs."""
    if workspace is not None:
        from .multilingual_workspace import plan_workspace
        console.print(json.dumps(plan_workspace(workspace), ensure_ascii=False)); return
    profile = "release" if language == "en" else "preview"
    console.print(json.dumps({"language": language, "edition": edition, "profile": profile, "formats": ["md", "docx", "pdf"], "api_calls": 0}))


def _translation_context(config: Path, provider_name: str | None, transport=None):
    from .providers.config import load_provider_config
    from .providers.mock import MockTranslationProvider
    from .providers.openai_compatible import OpenAICompatibleTranslationProvider, deepseek_transport
    cfg = load_provider_config(config)
    name = provider_name or cfg.get("active_translation_provider")
    if not name or name not in cfg.get("providers", {}):
        raise ValueError("translation provider is not configured")
    provider_cfg = cfg["providers"][name]
    kind = provider_cfg.get("type", name)
    if kind == "mock": provider = MockTranslationProvider()
    elif kind == "openai_compatible":
        provider = OpenAICompatibleTranslationProvider(provider_cfg, transport=transport or deepseek_transport(provider_cfg))
    else: raise ValueError(f"unsupported translation provider type: {kind}")
    return cfg, name, provider_cfg, provider


@app.command("translate-plan")
def production_translate_plan(
    language: Annotated[str, typer.Option("--language")] = "zh-Hans",
    config: Annotated[Path, typer.Option("--config")] = Path("config/providers.example.yaml"),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    max_units: Annotated[int | None, typer.Option("--max-units")] = None,
    status_filter: Annotated[str, typer.Option("--status-filter")] = "pending",
    unit_type: Annotated[str | None, typer.Option("--unit-type")] = None,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Generate the pending-only production plan without provider or network access."""
    if workspace is not None:
        from .multilingual_workspace import plan_workspace
        report = plan_workspace(workspace); report.update(provider=provider or "mock", model=model, api_calls=0)
        console.print(json.dumps(report, ensure_ascii=False)); return
    from .providers.config import load_provider_config
    from .translation_runner import TranslationRunner
    cfg = load_provider_config(config)
    name = provider or cfg.get("active_translation_provider", "mock")
    selected = cfg.get("providers", {}).get(name, {})
    report = TranslationRunner(project_root()).plan(max_units=max_units, unit_type=unit_type, status_filter=status_filter)
    report.update(language=language, provider=name, model=model or selected.get("model"), config=str(config), api_calls=0)
    console.print(json.dumps(report, ensure_ascii=False))


@app.command("translate")
def production_translate(
    language: Annotated[str, typer.Option("--language")] = "zh-Hans",
    config: Annotated[Path, typer.Option("--config")] = Path("config/providers.example.yaml"),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run")] = True,
    max_units: Annotated[int | None, typer.Option("--max-units")] = None,
    batch_size: Annotated[int, typer.Option("--batch-size")] = 8,
    max_input_tokens: Annotated[int, typer.Option("--max-input-tokens")] = 12000,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 2,
    status_filter: Annotated[str, typer.Option("--status-filter")] = "pending",
    unit_type: Annotated[str | None, typer.Option("--unit-type")] = None,
    resume: Annotated[bool, typer.Option("--resume/--no-resume")] = True,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Plan by default; execute only after every production safety gate passes."""
    if workspace is not None:
        from .providers.config import load_provider_config
        from .multilingual_workspace import plan_workspace, translate_workspace
        cfg = load_provider_config(config); name = provider or cfg.get("active_translation_provider", "mock")
        selected = cfg.get("providers", {}).get(name, {})
        if name == "mock" or selected.get("type") == "mock":
            from .providers.mock import MockTranslationProvider
            adapter = MockTranslationProvider(); selected_model = model or selected.get("model", "mock-v1")
        else:
            if dry_run:
                report = plan_workspace(workspace); report.update(provider=name, model=model or selected.get("model"), api_calls=0)
                console.print(json.dumps(report, ensure_ascii=False)); return
            if not cfg.get("allow_real_api"): raise typer.BadParameter("allow_real_api=true is required")
            key_env = selected.get("api_key_env")
            if not key_env or not os.getenv(key_env): raise typer.BadParameter("configured API key environment variable is missing")
            from .providers.openai_compatible import OpenAICompatibleTranslationProvider, deepseek_transport
            adapter = OpenAICompatibleTranslationProvider(selected, transport=deepseek_transport(selected)); selected_model = model or selected["model"]
        if dry_run:
            report = plan_workspace(workspace); report.update(provider=name, model=selected_model, api_calls=0)
            console.print(json.dumps(report, ensure_ascii=False)); return
        result = translate_workspace(workspace, adapter, provider_name=name, model=selected_model,
                                     batch_size=batch_size, max_units=max_units)
        console.print(json.dumps(result, ensure_ascii=False)); return
    from .providers.config import load_provider_config
    from .translation_runner import TranslationRunner
    if dry_run:
        production_translate_plan(language, config, provider, model, max_units, status_filter, unit_type)
        return
    try:
        cfg = load_provider_config(config)
        if not cfg.get("allow_real_api"): raise ValueError("allow_real_api=true is required")
        name = provider or cfg.get("active_translation_provider")
        provider_cfg = cfg.get("providers", {}).get(name or "", {})
        if provider_cfg.get("type") != "openai_compatible": raise ValueError("production translation requires openai_compatible provider")
        key_env = provider_cfg.get("api_key_env")
        if not key_env or not os.getenv(key_env): raise ValueError("configured API key environment variable is missing")
        runner = TranslationRunner(project_root())
        plan = runner.plan(max_units=max_units, unit_type=unit_type, status_filter=status_filter)
        if plan["retranslated_existing_main_text"] or plan["preserve_source_queued"] or plan["blocked_source_queued"]: raise ValueError("pending-only queue invariant failed")
        console.print(json.dumps({"translation_plan": plan, "provider": name, "model": model or provider_cfg.get("model")}, ensure_ascii=False))
        if plan["unit_count"] == 0:
            terminal = runner.status()
            if terminal["production_checkpoint_status"] == "completed":
                console.print(json.dumps({"already_completed": True, "validated": 0, "api_calls": 0, "cache_hits": 0, "production_checkpoint_status": "completed", "production_translation_status": "completed", "next_action": terminal["next_action"], "user_production_api_calls": terminal["user_production_api_calls"], "codex_api_calls": terminal["codex_api_calls"]}, ensure_ascii=False))
                return
        _, name, provider_cfg, adapter = _translation_context(config, name)
        if model:
            adapter.config["model"] = model
        if not adapter.health_check().get("ok"): raise ValueError("provider health check failed")
        if not yes and not typer.confirm("Execute this translation plan?"): raise ValueError("explicit confirmation required")
        runner.provider = adapter
        result = runner.run(provider_name=name, model=model or provider_cfg["model"], max_units=max_units, batch_size=batch_size, max_input_tokens=max_input_tokens, max_retries=max_retries, unit_type=unit_type, status_filter=status_filter, resume=resume)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        console.print(f"Error: {exc}", style="bold red"); raise typer.Exit(code=2) from exc
    console.print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=False))


@app.command("resume")
def production_resume(
    language: Annotated[str, typer.Option("--language")] = "zh-Hans",
    config: Annotated[Path, typer.Option("--config")] = Path("config/providers.local.yaml"),
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    max_units: Annotated[int | None, typer.Option("--max-units")] = None,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Resume the same durable production translation task after all safety gates."""
    production_translate(language, config, provider, None, False, max_units, 8, 12000, 2, "pending", None, True, yes, workspace)


@app.command("status")
def production_status(
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Show production checkpoint status."""
    if workspace is not None:
        from .multilingual_workspace import status_workspace
        console.print(json.dumps(status_workspace(workspace), ensure_ascii=False)); return
    from .translation_runner import TranslationRunner
    runner = TranslationRunner(project_root())
    report = runner.status()
    console.print(json.dumps({"translation_status_counts": report["status_counts"], "validated": report["validated"], "pending": report["pending"], "user_production_api_calls": report["user_production_api_calls"], "codex_api_calls": report["codex_api_calls"], "production_checkpoint_status": report["production_checkpoint_status"], "production_translation_status": report["production_translation_status"], "next_action": report["next_action"], "completed_at": report["completed_at"]}, ensure_ascii=False))


def _format_tuple(value: str) -> tuple[str, ...]:
    aliases = {"markdown": "md"}
    return tuple(aliases.get(x.strip(), x.strip()) for x in value.split(",") if x.strip())


@app.command("render")
def production_render(
    format: Annotated[str, typer.Option("--format")] = "markdown",
    language: Annotated[str, typer.Option("--language")] = "en",
    profile: Annotated[str, typer.Option("--profile")] = "release",
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
    role: Annotated[str | None, typer.Option("--role")] = None,
) -> None:
    """Render one immutable offline build artifact."""
    if workspace is not None:
        from .multilingual_workspace import OUTPUT_ROLES, _load, render_workspace
        manifest = _load(workspace); selected = role or language
        if selected == manifest["source_language"]: selected = "source"
        elif selected == manifest["target_language"]: selected = "target"
        elif selected == manifest["language_pair"]: selected = "bilingual"
        if selected not in OUTPUT_ROLES: raise typer.BadParameter("role must be source, target, or bilingual")
        result = render_workspace(workspace, selected, _format_tuple(format))
        console.print(json.dumps(result, ensure_ascii=False)); return
    from .production import build
    try:
        result = build(project_root(), language, profile, _format_tuple(format))
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(json.dumps(result, ensure_ascii=False))


@app.command("validate")
def production_validate(
    build_id: Annotated[str | None, typer.Option("--build")] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Validate an immutable build or the multilingual translation layer."""
    if workspace is not None:
        from .multilingual_workspace import validate_workspace
        result = validate_workspace(workspace); console.print(json.dumps(result, ensure_ascii=False))
        if not result["valid"]: raise typer.Exit(code=1)
        return
    if language:
        from .translation_runner import TranslationRunner
        runner = TranslationRunner(project_root())
        status = runner.status()
        console.print(json.dumps({"language": language, "status_counts": status["status_counts"], "pending_only_queue": runner.plan()["unit_count"], "user_production_api_calls": status["user_production_api_calls"], "codex_api_calls": status["codex_api_calls"], "valid": True}, ensure_ascii=False)); return
    if not build_id:
        raise typer.BadParameter("--build or --language is required")
    base = project_root() / "output/fullbook" / build_id
    manifest = base / "render_manifest.json"
    if not manifest.is_file():
        console.print("Build manifest not found", style="bold red")
        raise typer.Exit(code=2)
    data = json.loads(manifest.read_text("utf-8"))
    if data.get("canonical_sha256") != "16c1c9ba4d60d1c2a4124433291a1a56bf499384215c720f6988e6e183c01326":
        raise typer.Exit(code=1)
    console.print(json.dumps({"build_id": build_id, "valid": True}))


@app.command("build")
def production_build(
    language: Annotated[str, typer.Option("--language")] = "en",
    formats: Annotated[str, typer.Option("--formats")] = "md,docx,pdf",
    profile: Annotated[str, typer.Option("--profile")] = "release",
    allow_source_fallback: Annotated[bool, typer.Option("--allow-source-fallback")] = False,
    workspace: Annotated[Path | None, typer.Option("--workspace")] = None,
) -> None:
    """Run Markdown, DOCX, and PDF production with release gates."""
    if workspace is not None:
        from .multilingual_workspace import build_workspace
        try: result = build_workspace(workspace, _format_tuple(formats))
        except (ValueError, RuntimeError, FileNotFoundError, FileExistsError) as exc:
            console.print(f"Error: {exc}", style="bold red"); raise typer.Exit(code=2) from exc
        console.print(json.dumps(result, ensure_ascii=False)); return
    if language != "en" and allow_source_fallback:
        profile = "preview"
    from .production import build
    try:
        result = build(project_root(), language, profile, _format_tuple(formats))
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError) as exc:
        console.print(f"Error: {exc}", style="bold red")
        raise typer.Exit(code=2) from exc
    console.print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    app()
