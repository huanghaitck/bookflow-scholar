"""Offline PDF page rendering, cache reuse, status, and validation."""

from __future__ import annotations

import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import fitz
from PIL import Image
from pydantic import BaseModel, Field

from .io_utils import atomic_write_json, load_json, sha256_file, stable_hash
from .paths import ProjectSettings, project_root, resolve_project_path
from .schemas import PageManifest, PageRecord, SCHEMA_VERSION


RENDERER = "PyMuPDF"
RENDERER_VERSION = fitz.VersionBind


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_pages(value: str) -> list[int]:
    """Parse page expressions such as ``1-3,5,8-9`` into sorted unique pages."""

    pages: set[int] = set()
    if not value or not value.strip():
        raise ValueError("Page range cannot be empty")
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Invalid page range: {value}")
        if "-" in part:
            pieces = part.split("-", maxsplit=1)
            if len(pieces) != 2 or not all(piece.strip().isdigit() for piece in pieces):
                raise ValueError(f"Invalid page range: {value}")
            start, end = (int(piece.strip()) for piece in pieces)
            if start < 1 or end < start:
                raise ValueError(f"Invalid page range: {value}")
            pages.update(range(start, end + 1))
        elif part.isdigit() and int(part) >= 1:
            pages.add(int(part))
        else:
            raise ValueError(f"Invalid page range: {value}")
    return sorted(pages)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_.")
    return cleaned or "document"


def _normalized_color_mode(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"L", "GRAY", "GREY", "GRAYSCALE"}:
        return "GRAYSCALE"
    if normalized == "RGB":
        return normalized
    raise ValueError("Color mode must be RGB or grayscale")


class PipelineContext(BaseModel):
    source_pdf: str
    source_pdf_sha256: str
    document_id: str
    document_slug: str
    page_count: int
    pages: list[int]
    dpi: int
    color_mode: str
    image_format: str
    render_profile_id: str
    image_directory: str
    record_directory: str
    manifest_path: str
    cache_index_path: str


class RenderRunResult(BaseModel):
    document_id: str
    source_pdf: str
    actual_page_count: int
    selected_pages: list[int]
    rendered_pages: list[int] = Field(default_factory=list)
    cached_pages: list[int] = Field(default_factory=list)
    skipped_pages: list[int] = Field(default_factory=list)
    failed_pages: list[int] = Field(default_factory=list)
    warnings: dict[int, list[str]] = Field(default_factory=dict)
    errors: dict[int, str] = Field(default_factory=dict)
    output_directory: str
    manifest_path: str
    cache_index_path: str
    elapsed_seconds: float
    offline: bool = True
    api_called: bool = False


class PageStatusReport(BaseModel):
    source_pdf: str
    source_pdf_sha256: str
    document_id: str
    actual_page_count: int
    selected_pages: list[int]
    rendered_pages: list[int] = Field(default_factory=list)
    missing_pages: list[int] = Field(default_factory=list)
    orphan_image_pages: list[int] = Field(default_factory=list)
    missing_image_pages: list[int] = Field(default_factory=list)
    hash_anomaly_pages: list[int] = Field(default_factory=list)
    invalid_image_pages: list[int] = Field(default_factory=list)
    invalid_manifest_pages: list[int] = Field(default_factory=list)
    duplicate_pages: list[int] = Field(default_factory=list)
    manifest_exists: bool
    manifest_complete: bool
    source_hash_matches: bool
    page_numbers_continuous: bool
    cache_status: str
    current_parameters: dict[str, object]
    ready_for_vision: bool
    manifest_path: str


def _validate_scope(
    pdf_path: Path,
    settings: ProjectSettings,
    pages_requested: bool,
    root: Path,
) -> None:
    sample = resolve_project_path(settings.sample_pdf, root=root)
    protected_full = resolve_project_path(settings.source_pdf, root=root)
    if pdf_path == protected_full:
        raise PermissionError(
            "Phase 1B prohibits rendering or page processing of the configured full PDF"
        )
    if pdf_path != sample and not pages_requested:
        raise ValueError(
            "A page range is required when the input is not the configured sample PDF"
        )


def build_context(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    pages: str | list[int] | None = None,
    dpi: int | None = None,
    color_mode: str | None = None,
    image_format: str | None = None,
    root: Path | None = None,
    image_root: Path | None = None,
    manifest_root: Path | None = None,
    cache_root: Path | None = None,
) -> PipelineContext:
    root = (root or project_root()).resolve()
    source = resolve_project_path(pdf_path, root=root)
    if not source.is_file():
        raise FileNotFoundError(f"PDF file not found: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError(f"File is not a PDF: {source}")

    pages_requested = pages is not None
    _validate_scope(source, settings, pages_requested, root)
    with fitz.open(source) as document:
        page_count = document.page_count

    if pages is None:
        start, end = settings.sample_page_range
        selected = list(range(start, end + 1))
    elif isinstance(pages, str):
        selected = parse_pages(pages)
    else:
        selected = sorted(set(pages))
    if not selected:
        raise ValueError("At least one PDF page must be selected")
    invalid = [page for page in selected if page < 1 or page > page_count]
    if invalid:
        raise ValueError(
            f"Selected pages are outside the actual PDF range 1-{page_count}: {invalid}"
        )

    resolved_dpi = dpi if dpi is not None else settings.render_dpi
    if resolved_dpi < 72 or resolved_dpi > 600:
        raise ValueError("DPI must be between 72 and 600")
    resolved_format = (image_format or settings.render_format).lower().lstrip(".")
    if resolved_format != "png":
        raise ValueError("Phase 1B supports PNG rendering only")
    resolved_color = _normalized_color_mode(color_mode or settings.render_color_mode)
    source_hash = sha256_file(source)
    document_id = f"doc_{source_hash[:16]}"
    profile_payload = {
        "schema_version": SCHEMA_VERSION,
        "source_pdf_sha256": source_hash,
        "dpi": resolved_dpi,
        "color_mode": resolved_color,
        "image_format": resolved_format,
        "renderer": RENDERER,
        "renderer_version": RENDERER_VERSION,
    }
    profile_id = stable_hash(profile_payload)[:16]
    slug = _slug(source.stem)
    image_base = image_root or resolve_project_path(settings.page_image_directory, root=root)
    manifest_base = manifest_root or resolve_project_path(settings.manifest_directory, root=root)
    cache_base = cache_root or resolve_project_path(settings.cache_directory, root=root)
    image_directory = image_base / slug / f"profile_{profile_id}"
    profile_manifest_dir = manifest_base / slug / f"profile_{profile_id}"
    return PipelineContext(
        source_pdf=str(source),
        source_pdf_sha256=source_hash,
        document_id=document_id,
        document_slug=slug,
        page_count=page_count,
        pages=selected,
        dpi=resolved_dpi,
        color_mode=resolved_color,
        image_format=resolved_format,
        render_profile_id=profile_id,
        image_directory=str(image_directory),
        record_directory=str(profile_manifest_dir / "pages"),
        manifest_path=str(profile_manifest_dir / "manifest.json"),
        cache_index_path=str(cache_base / "render" / slug / f"profile_{profile_id}.json"),
    )


def _page_paths(context: PipelineContext, page_number: int) -> tuple[Path, Path]:
    name = f"page_{page_number:04d}"
    image = Path(context.image_directory) / f"{name}.{context.image_format}"
    record = Path(context.record_directory) / f"{name}.json"
    return image, record


def _cache_key(context: PipelineContext, page_number: int) -> str:
    return stable_hash(
        {
            "source_pdf_sha256": context.source_pdf_sha256,
            "pdf_page": page_number,
            "dpi": context.dpi,
            "color_mode": context.color_mode,
            "image_format": context.image_format,
            "renderer": RENDERER,
            "renderer_version": RENDERER_VERSION,
        }
    )


def _load_record(path: Path) -> PageRecord:
    return PageRecord.model_validate(load_json(path))


def _record_cache_valid(
    record_path: Path,
    image_path: Path,
    context: PipelineContext,
    page_number: int,
) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    if image_path.exists() and not record_path.exists():
        return False, ["orphan_image_without_page_record"]
    if record_path.exists() and not image_path.exists():
        return False, ["page_record_exists_but_image_missing"]
    if not record_path.exists() and not image_path.exists():
        return False, warnings
    try:
        record = _load_record(record_path)
    except Exception as exc:
        return False, [f"invalid_page_record:{type(exc).__name__}"]
    if record.cache_key != _cache_key(context, page_number):
        warnings.append("cache_key_mismatch")
    if record.source_pdf_sha256 != context.source_pdf_sha256:
        warnings.append("source_pdf_hash_mismatch")
    if record.pdf_page != page_number:
        warnings.append("page_number_mismatch")
    try:
        if sha256_file(image_path) != record.image_sha256:
            warnings.append("image_hash_mismatch")
        with Image.open(image_path) as image:
            image.verify()
    except Exception as exc:
        warnings.append(f"invalid_image:{type(exc).__name__}")
    return not warnings, warnings


def _render_page_image(
    page: fitz.Page,
    destination: Path,
    dpi: int,
    color_mode: str,
) -> tuple[int, int]:
    """Render one page to a temporary PNG and atomically install it."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    colorspace = fitz.csRGB if color_mode == "RGB" else fitz.csGRAY
    pixmap = page.get_pixmap(dpi=dpi, colorspace=colorspace, alpha=False)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".png", dir=destination.parent
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    try:
        pixmap.save(temporary)
        with Image.open(temporary) as image:
            image.verify()
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return pixmap.width, pixmap.height


def _write_run_log(settings: ProjectSettings, root: Path, payload: dict[str, object]) -> str:
    log_root = resolve_project_path(settings.log_directory, root=root)
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    path = log_root / f"render_{stamp}.json"
    atomic_write_json(path, payload)
    return str(path)


def render_pages(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    pages: str | list[int] | None = None,
    dpi: int | None = None,
    color_mode: str | None = None,
    image_format: str | None = None,
    resume: bool | None = None,
    force: bool | None = None,
    root: Path | None = None,
    image_root: Path | None = None,
    manifest_root: Path | None = None,
    cache_root: Path | None = None,
    max_pages_this_run: int | None = None,
    render_page_func: Callable[[fitz.Page, Path, int, str], tuple[int, int]] | None = None,
) -> RenderRunResult:
    """Render selected pages locally with page-granular cache and recovery."""

    started = time.perf_counter()
    root = (root or project_root()).resolve()
    context = build_context(
        pdf_path,
        settings,
        pages=pages,
        dpi=dpi,
        color_mode=color_mode,
        image_format=image_format,
        root=root,
        image_root=image_root,
        manifest_root=manifest_root,
        cache_root=cache_root,
    )
    use_resume = settings.resume if resume is None else resume
    use_force = settings.force if force is None else force
    image_dir = Path(context.image_directory)
    record_dir = Path(context.record_directory)
    manifest_path = Path(context.manifest_path)
    if not use_resume and not use_force and (
        any(image_dir.glob("page_*.png")) or any(record_dir.glob("page_*.json"))
    ):
        raise RuntimeError(
            "Existing page outputs were found; use --resume to continue or --force to rebuild"
        )
    image_dir.mkdir(parents=True, exist_ok=True)
    record_dir.mkdir(parents=True, exist_ok=True)
    render_page_func = render_page_func or _render_page_image
    rendered: list[int] = []
    cached: list[int] = []
    skipped: list[int] = []
    failed: list[int] = []
    warnings: dict[int, list[str]] = {}
    errors: dict[int, str] = {}
    processed_this_run = 0

    with fitz.open(context.source_pdf) as document:
        for page_number in context.pages:
            if max_pages_this_run is not None and processed_this_run >= max_pages_this_run:
                skipped.extend(
                    number for number in context.pages if number not in rendered + cached + failed
                )
                break
            image_path, record_path = _page_paths(context, page_number)
            valid, page_warnings = _record_cache_valid(
                record_path, image_path, context, page_number
            )
            if valid and not use_force:
                cached.append(page_number)
                processed_this_run += 1
                continue
            if page_warnings:
                warnings[page_number] = page_warnings
            try:
                page = document.load_page(page_number - 1)
                width, height = render_page_func(
                    page, image_path, context.dpi, context.color_mode
                )
                text = page.get_text("text") or ""
                record = PageRecord(
                    document_id=context.document_id,
                    source_pdf=context.source_pdf,
                    source_pdf_sha256=context.source_pdf_sha256,
                    pdf_page=page_number,
                    pdf_page_index=page_number - 1,
                    printed_page=None,
                    page_count=context.page_count,
                    image_path=str(image_path.resolve()),
                    image_sha256=sha256_file(image_path),
                    image_width=width,
                    image_height=height,
                    dpi=context.dpi,
                    color_mode=context.color_mode,
                    image_format=context.image_format,
                    text_layer_available=bool(text),
                    text_layer_character_count=len(text),
                    rendered_at=utc_now(),
                    renderer=RENDERER,
                    renderer_version=RENDERER_VERSION,
                    cache_key=_cache_key(context, page_number),
                    warnings=page_warnings,
                )
                atomic_write_json(record_path, record)
                rendered.append(page_number)
            except Exception as exc:
                failed.append(page_number)
                errors[page_number] = f"{type(exc).__name__}: {exc}"
            processed_this_run += 1

    existing_records: list[PageRecord] = []
    for path in sorted(record_dir.glob("page_*.json")):
        try:
            existing_records.append(_load_record(path))
        except Exception:
            continue
    previous_created = utc_now()
    if manifest_path.exists():
        try:
            previous_created = PageManifest.model_validate(
                load_json(manifest_path)
            ).created_at
        except Exception:
            pass
    manifest = PageManifest(
        document_id=context.document_id,
        source_pdf=context.source_pdf,
        source_pdf_sha256=context.source_pdf_sha256,
        page_count=context.page_count,
        render_profile_id=context.render_profile_id,
        dpi=context.dpi,
        color_mode=context.color_mode,
        image_format=context.image_format,
        renderer=RENDERER,
        renderer_version=RENDERER_VERSION,
        selected_pages=sorted({record.pdf_page for record in existing_records}),
        page_record_paths=[
            str((record_dir / f"page_{record.pdf_page:04d}.json").resolve())
            for record in sorted(existing_records, key=lambda item: item.pdf_page)
        ],
        failed_pages=failed,
        created_at=previous_created,
        updated_at=utc_now(),
    )
    atomic_write_json(manifest_path, manifest)
    elapsed = round(time.perf_counter() - started, 3)
    result = RenderRunResult(
        document_id=context.document_id,
        source_pdf=context.source_pdf,
        actual_page_count=context.page_count,
        selected_pages=context.pages,
        rendered_pages=rendered,
        cached_pages=cached,
        skipped_pages=sorted(set(skipped)),
        failed_pages=failed,
        warnings=warnings,
        errors=errors,
        output_directory=context.image_directory,
        manifest_path=context.manifest_path,
        cache_index_path=context.cache_index_path,
        elapsed_seconds=elapsed,
    )
    atomic_write_json(
        context.cache_index_path,
        {
            "schema_version": SCHEMA_VERSION,
            "document_id": context.document_id,
            "render_profile_id": context.render_profile_id,
            "manifest_path": context.manifest_path,
            "last_run": result.model_dump(mode="json"),
        },
    )
    _write_run_log(
        settings,
        root,
        {
            "run_id": str(uuid.uuid4()),
            "operation": "render_pages",
            "result": result.model_dump(mode="json"),
        },
    )
    return result


def page_status(
    pdf_path: str | Path,
    settings: ProjectSettings,
    *,
    pages: str | list[int] | None = None,
    dpi: int | None = None,
    color_mode: str | None = None,
    image_format: str | None = None,
    root: Path | None = None,
    image_root: Path | None = None,
    manifest_root: Path | None = None,
    cache_root: Path | None = None,
) -> PageStatusReport:
    root = (root or project_root()).resolve()
    context = build_context(
        pdf_path,
        settings,
        pages=pages,
        dpi=dpi,
        color_mode=color_mode,
        image_format=image_format,
        root=root,
        image_root=image_root,
        manifest_root=manifest_root,
        cache_root=cache_root,
    )
    rendered: list[int] = []
    missing: list[int] = []
    orphan: list[int] = []
    missing_image: list[int] = []
    hash_anomaly: list[int] = []
    invalid_images: list[int] = []
    invalid_records: list[int] = []
    source_hash_matches = True
    observed_pages: list[int] = []
    for page_number in context.pages:
        image_path, record_path = _page_paths(context, page_number)
        if image_path.exists() and not record_path.exists():
            orphan.append(page_number)
            missing.append(page_number)
            continue
        if not record_path.exists():
            missing.append(page_number)
            continue
        try:
            record = _load_record(record_path)
        except Exception:
            invalid_records.append(page_number)
            continue
        observed_pages.append(record.pdf_page)
        if record.source_pdf_sha256 != context.source_pdf_sha256:
            source_hash_matches = False
        if not image_path.exists():
            missing_image.append(page_number)
            continue
        try:
            if sha256_file(image_path) != record.image_sha256:
                hash_anomaly.append(page_number)
                continue
            with Image.open(image_path) as image:
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("invalid dimensions")
                image.verify()
        except Exception:
            invalid_images.append(page_number)
            continue
        if record.cache_key != _cache_key(context, page_number):
            hash_anomaly.append(page_number)
            continue
        rendered.append(page_number)

    duplicates = sorted({page for page in observed_pages if observed_pages.count(page) > 1})
    continuous = context.pages == list(range(min(context.pages), max(context.pages) + 1))
    manifest_exists = Path(context.manifest_path).is_file()
    manifest_complete = False
    if manifest_exists:
        try:
            manifest = PageManifest.model_validate(load_json(context.manifest_path))
            manifest_complete = (
                manifest.source_pdf_sha256 == context.source_pdf_sha256
                and set(context.pages).issubset(set(manifest.selected_pages))
            )
        except Exception:
            manifest_complete = False
    ready = not any(
        [
            missing,
            orphan,
            missing_image,
            hash_anomaly,
            invalid_images,
            invalid_records,
            duplicates,
        ]
    ) and manifest_complete and source_hash_matches and continuous
    if ready:
        cache_state = "complete"
    elif rendered:
        cache_state = "partial_or_invalid"
    else:
        cache_state = "empty_or_invalid"
    return PageStatusReport(
        source_pdf=context.source_pdf,
        source_pdf_sha256=context.source_pdf_sha256,
        document_id=context.document_id,
        actual_page_count=context.page_count,
        selected_pages=context.pages,
        rendered_pages=rendered,
        missing_pages=missing,
        orphan_image_pages=orphan,
        missing_image_pages=missing_image,
        hash_anomaly_pages=sorted(set(hash_anomaly)),
        invalid_image_pages=invalid_images,
        invalid_manifest_pages=invalid_records,
        duplicate_pages=duplicates,
        manifest_exists=manifest_exists,
        manifest_complete=manifest_complete,
        source_hash_matches=source_hash_matches,
        page_numbers_continuous=continuous,
        cache_status=cache_state,
        current_parameters={
            "dpi": context.dpi,
            "color_mode": context.color_mode,
            "image_format": context.image_format,
            "render_profile_id": context.render_profile_id,
        },
        ready_for_vision=ready,
        manifest_path=context.manifest_path,
    )


def validate_manifest_file(
    manifest_path: str | Path,
    *,
    source_pdf: str | Path | None = None,
) -> PageStatusReport:
    """Validate an existing manifest, optionally against a supplied source PDF."""

    manifest_file = Path(manifest_path).resolve(strict=False)
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Manifest file not found: {manifest_file}")
    manifest = PageManifest.model_validate(load_json(manifest_file))
    source = Path(source_pdf or manifest.source_pdf).resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(f"PDF file not found: {source}")
    current_source_hash = sha256_file(source)
    records: list[PageRecord] = []
    invalid_records: list[int] = []
    for record_path_value in manifest.page_record_paths:
        record_path = Path(record_path_value)
        try:
            records.append(_load_record(record_path))
        except Exception:
            match = re.search(r"(\d+)", record_path.stem)
            invalid_records.append(int(match.group(1)) if match else 0)
    pages = sorted(record.pdf_page for record in records)
    duplicate_pages = sorted({page for page in pages if pages.count(page) > 1})
    expected = manifest.selected_pages
    missing = sorted(set(expected) - set(pages))
    missing_images: list[int] = []
    hash_anomalies: list[int] = []
    invalid_images: list[int] = []
    rendered: list[int] = []
    for record in records:
        image_path = Path(record.image_path)
        if not image_path.is_file():
            missing_images.append(record.pdf_page)
            continue
        if sha256_file(image_path) != record.image_sha256:
            hash_anomalies.append(record.pdf_page)
            continue
        try:
            with Image.open(image_path) as image:
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("invalid dimensions")
                image.verify()
        except Exception:
            invalid_images.append(record.pdf_page)
            continue
        rendered.append(record.pdf_page)
    continuous = expected == list(range(min(expected), max(expected) + 1)) if expected else False
    source_matches = current_source_hash == manifest.source_pdf_sha256
    ready = not any(
        [missing, missing_images, hash_anomalies, invalid_images, invalid_records, duplicate_pages]
    ) and continuous and source_matches
    return PageStatusReport(
        source_pdf=str(source),
        source_pdf_sha256=current_source_hash,
        document_id=manifest.document_id,
        actual_page_count=manifest.page_count,
        selected_pages=expected,
        rendered_pages=rendered,
        missing_pages=missing,
        missing_image_pages=missing_images,
        hash_anomaly_pages=hash_anomalies,
        invalid_image_pages=invalid_images,
        invalid_manifest_pages=invalid_records,
        duplicate_pages=duplicate_pages,
        manifest_exists=True,
        manifest_complete=not missing and not invalid_records,
        source_hash_matches=source_matches,
        page_numbers_continuous=continuous,
        cache_status="complete" if ready else "partial_or_invalid",
        current_parameters={
            "dpi": manifest.dpi,
            "color_mode": manifest.color_mode,
            "image_format": manifest.image_format,
            "render_profile_id": manifest.render_profile_id,
        },
        ready_for_vision=ready,
        manifest_path=str(manifest_file),
    )
