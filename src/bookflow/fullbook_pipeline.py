"""Thin, resumable Phase 6 orchestration around the verified sample components."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import time
from types import SimpleNamespace
from typing import Any, Callable, Literal

import fitz
from pydantic import BaseModel, ConfigDict, Field

from .io_utils import atomic_write_json, load_json, sha256_file, sha256_text, stable_hash
from .page_pipeline import render_pages
from .phase2a1 import normalize_preserved_response_v11
from .paths import ProjectSettings, project_root, resolve_project_path
from .secret_store import (
    api_key_status, load_api_key, load_translation_api_key, translation_api_key_status,
)
from .vision_provider import ZhipuOpenAICompatibleProvider
from .phase2a1 import VisionNormalizedPageV11
from .phase2b2_schemas import (
    AutomatedBoundary, AutomatedLogicalBlock, AutomatedPageRecord, SourceFragment,
)
from .phase2b_calls import _boundary_from_response
from .phase2b_schemas import BoundaryDecision
from .phase3c4 import (
    Phase3C4TranslationResult, Phase3C4TranslationUnit, TRANSLATABLE_TYPES, TITLE_TYPES,
    _profile_text, normalize_phase3c4_translation,
)
from .translation_provider import DeepSeekOpenAICompatibleProvider
from .automated_reconstruction import (
    _edge_relation,
    _fragment_id,
    _similarity,
    _within_page_continuation,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def partition_pages(actual_page_count: int, batch_size: int) -> list[list[int]]:
    if actual_page_count < 1:
        raise ValueError("actual_page_count must be positive")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    pages = list(range(1, actual_page_count + 1))
    return [pages[index:index + batch_size] for index in range(0, len(pages), batch_size)]


class FullbookPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_pdf: str
    source_pdf_sha256: str
    source_size_bytes: int
    actual_page_count: int = Field(ge=1)
    potential_boundary_count: int = Field(ge=0)
    unique_page_sizes: list[list[float]]
    page_batch_size: int
    page_batch_count: int
    translation_checkpoint_interval: int
    exact_page_cache_hits: int
    estimated_new_single_calls: int
    estimated_pair_calls_lower: int
    estimated_pair_calls_upper: int
    estimated_triple_calls_upper: int
    estimated_glm_total_calls_lower: int
    estimated_glm_total_calls_upper: int
    estimated_glm_tokens_lower: int
    estimated_glm_tokens_upper: int
    estimated_glm_cost_lower_cny: float
    estimated_glm_cost_upper_cny: float
    estimated_deepseek_calls_lower: int
    estimated_deepseek_calls_upper: int
    estimated_deepseek_tokens_lower: int
    estimated_deepseek_tokens_upper: int
    estimated_deepseek_cost_lower_cny: float
    estimated_deepseek_cost_upper_cny: float
    vision_api_key_set: bool
    translation_api_key_set: bool
    checkpoint_path: str
    source_document_path: str
    bilingual_document_path: str
    candidate_directory: str
    final_directory: str
    blockers: list[str]
    ready: bool


class FullbookRenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rendered_pages: list[int]
    cached_pages: list[int]
    failed_pages: list[int]
    page_record_paths: list[str]
    manifest_path: str
    elapsed_seconds: float


class FullbookVisionCacheImportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imported_pages: list[int]
    skipped_nonmatching_pages: int
    normalized_paths: list[str]
    api_calls_this_run: Literal[0] = 0


class FullbookVisionBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_pages: list[int]
    completed_pages: list[int]
    cached_pages: list[int]
    failed_pages: list[int]
    raw_paths: list[str]
    normalized_paths: list[str]
    api_calls_this_run: int = Field(ge=0)
    total_tokens_this_run: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)


class FullbookVisionRecoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recovered_pages: list[int]
    still_failed_pages: list[int]
    normalized_paths: list[str]
    api_calls_this_run: Literal[0] = 0


class FullbookAutomatedPageBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_count: int
    fragment_count: int
    partial_coverage_pages: list[int]
    output_path: str


class FullbookBoundaryBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["pair", "triple"]
    requested_boundaries: list[str]
    completed_boundaries: list[str]
    cached_boundaries: list[str]
    unresolved_boundaries: list[str]
    failed_boundaries: list[str]
    normalized_paths: list[str]
    api_calls_this_run: int = Field(ge=0)
    total_tokens_this_run: int = Field(ge=0)


class FullbookTranslationBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[Phase3C4TranslationResult]
    api_calls_this_run: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    failed_block_ids: list[str]
    pending_block_ids: list[str]
    request_paths: list[str]
    total_tokens_this_run: int = Field(ge=0)


class FullbookCheckpointStore:
    """Small atomic stage/item checkpoint tied to one immutable source PDF hash."""

    def __init__(self, path: str | Path, *, source_pdf_sha256: str) -> None:
        self.path = Path(path)
        if self.path.is_file():
            payload = load_json(self.path)
            if payload.get("source_pdf_sha256") != source_pdf_sha256:
                raise RuntimeError("source PDF hash changed since checkpoint creation")
            self._payload = payload
        else:
            self._payload = {
                "schema_version": "fullbook-checkpoint-1.0",
                "source_pdf_sha256": source_pdf_sha256,
                "completed": {},
                "quarantine": {},
                "api_calls": {"single": 0, "pair": 0, "triple": 0, "translation": 0},
                "created_at": _now(),
                "updated_at": _now(),
            }

    def is_completed(self, stage: str, item_id: str) -> bool:
        return item_id in self._payload.get("completed", {}).get(stage, [])

    def mark_completed(self, stage: str, item_id: str) -> None:
        items = self._payload.setdefault("completed", {}).setdefault(stage, [])
        if item_id not in items:
            items.append(item_id)
            items.sort()
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)

    def mark_quarantine(self, stage: str, item_id: str, error: str) -> None:
        self._payload.setdefault("quarantine", {}).setdefault(stage, {})[item_id] = {
            "error": error,
            "recorded_at": _now(),
        }
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)

    def increment_api_call(self, kind: Literal["single", "pair", "triple", "translation"]) -> None:
        calls = self._payload.setdefault("api_calls", {})
        calls[kind] = int(calls.get(kind, 0)) + 1
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)

    def clear_quarantine(self, stage: str, item_id: str) -> None:
        stage_items = self._payload.setdefault("quarantine", {}).setdefault(stage, {})
        stage_items.pop(item_id, None)
        self._payload["updated_at"] = _now()
        atomic_write_json(self.path, self._payload)

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self._payload)


def _valid_page_cache_count(settings: ProjectSettings, root: Path) -> int:
    cache = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision" / "cache"
    count = 0
    for path in cache.glob("*.json") if cache.is_dir() else []:
        try:
            payload = load_json(path)
            if payload.get("status") == "completed" and payload.get("request_fingerprint"):
                count += 1
        except Exception:
            continue
    return count


def build_fullbook_preflight(
    settings: ProjectSettings,
    *,
    root: Path | None = None,
    source_pdf: str | Path | None = None,
) -> FullbookPreflight:
    root = (root or project_root()).resolve()
    source = resolve_project_path(source_pdf or settings.source_pdf, root=root)
    if not source.is_file():
        raise FileNotFoundError(f"Full PDF not found: {source}")
    source_hash = sha256_file(source)
    with fitz.open(source) as document:
        if document.page_count < 1:
            raise ValueError("Full PDF contains no pages")
        actual = document.page_count
        sizes = sorted({
            (round(document[index].rect.width, 2), round(document[index].rect.height, 2))
            for index in range(actual)
        })
    boundaries = actual - 1
    cached = min(actual, _valid_page_cache_count(settings, root))
    singles = actual - cached
    pair_lower, pair_upper = 0, boundaries
    triple_upper = boundaries
    glm_calls_lower = singles
    glm_calls_upper = singles + pair_upper + triple_upper
    glm_tokens_lower = singles * 3_000
    glm_tokens_upper = singles * 8_000 + pair_upper * 12_000 + triple_upper * 18_000
    translation_lower, translation_upper = actual, actual * 3
    deepseek_tokens_lower = translation_lower * 1_588
    deepseek_tokens_upper = translation_upper * 1_588
    vision_key, _ = api_key_status(settings, root)
    translation_key, _ = translation_api_key_status(settings, root)
    blockers: list[str] = []
    if settings.vision_automatic_retry or settings.translation_automatic_retry:
        blockers.append("automatic retry must remain disabled")
    checkpoint = resolve_project_path(settings.fullbook_checkpoint_path, root=root)
    return FullbookPreflight(
        source_pdf=str(source), source_pdf_sha256=source_hash,
        source_size_bytes=source.stat().st_size, actual_page_count=actual,
        potential_boundary_count=boundaries,
        unique_page_sizes=[[width, height] for width, height in sizes],
        page_batch_size=settings.processing_page_batch_size,
        page_batch_count=len(partition_pages(actual, settings.processing_page_batch_size)),
        translation_checkpoint_interval=settings.translation_checkpoint_interval,
        exact_page_cache_hits=cached, estimated_new_single_calls=singles,
        estimated_pair_calls_lower=pair_lower, estimated_pair_calls_upper=pair_upper,
        estimated_triple_calls_upper=triple_upper,
        estimated_glm_total_calls_lower=glm_calls_lower,
        estimated_glm_total_calls_upper=glm_calls_upper,
        estimated_glm_tokens_lower=glm_tokens_lower,
        estimated_glm_tokens_upper=glm_tokens_upper,
        estimated_glm_cost_lower_cny=round(glm_tokens_lower / 1_000_000 * 1.0, 3),
        estimated_glm_cost_upper_cny=round(glm_tokens_upper / 1_000_000 * 6.0, 3),
        estimated_deepseek_calls_lower=translation_lower,
        estimated_deepseek_calls_upper=translation_upper,
        estimated_deepseek_tokens_lower=deepseek_tokens_lower,
        estimated_deepseek_tokens_upper=deepseek_tokens_upper,
        estimated_deepseek_cost_lower_cny=round(deepseek_tokens_lower / 1_000_000 * 2.0, 3),
        estimated_deepseek_cost_upper_cny=round(deepseek_tokens_upper / 1_000_000 * 6.0, 3),
        vision_api_key_set=vision_key, translation_api_key_set=translation_key,
        checkpoint_path=str(checkpoint),
        source_document_path=str(resolve_project_path(settings.fullbook_source_document_path, root=root)),
        bilingual_document_path=str(resolve_project_path(settings.fullbook_bilingual_document_path, root=root)),
        candidate_directory=str(resolve_project_path(settings.fullbook_candidate_directory, root=root)),
        final_directory=str(resolve_project_path(settings.fullbook_final_directory, root=root)),
        blockers=blockers, ready=not blockers,
    )


def render_fullbook_pages(
    settings: ProjectSettings,
    *,
    root: Path | None = None,
    source_pdf: str | Path | None = None,
    pages: list[int],
) -> FullbookRenderResult:
    """Render authorized full-book pages through the existing page cache engine."""

    root = (root or project_root()).resolve()
    source = resolve_project_path(source_pdf or settings.source_pdf, root=root)
    fullbook_root = resolve_project_path(settings.fullbook_data_directory, root=root)
    # The Phase 1B renderer intentionally rejects settings.source_pdf.  Phase 6
    # supplies an explicit page list and isolated roots, so a copied settings
    # object removes only that historical phase guard without weakening defaults.
    production_settings = settings.model_copy(update={
        "source_pdf": "__phase6_authorized_fullbook__",
        "page_image_directory": str(fullbook_root / "pages"),
        "manifest_directory": str(fullbook_root / "page_manifests"),
        "cache_directory": str(fullbook_root / "cache"),
        "log_directory": str(fullbook_root / "logs"),
    })
    result = render_pages(
        source,
        production_settings,
        pages=pages,
        dpi=settings.render_dpi,
        color_mode=settings.render_color_mode,
        image_format=settings.render_format,
        resume=True,
        force=False,
        root=root,
        image_root=fullbook_root / "pages",
        manifest_root=fullbook_root / "page_manifests",
        cache_root=fullbook_root / "cache",
    )
    manifest = load_json(result.manifest_path)
    return FullbookRenderResult(
        rendered_pages=result.rendered_pages,
        cached_pages=result.cached_pages,
        failed_pages=result.failed_pages,
        page_record_paths=[str(path) for path in manifest.get("page_record_paths", [])],
        manifest_path=result.manifest_path,
        elapsed_seconds=result.elapsed_seconds,
    )


def _fullbook_page_records(settings: ProjectSettings, root: Path) -> list[dict[str, Any]]:
    base = resolve_project_path(settings.fullbook_data_directory, root=root) / "page_manifests"
    records: list[dict[str, Any]] = []
    for path in sorted(base.rglob("page_*.json")) if base.is_dir() else []:
        payload = load_json(path)
        if {"document_id", "pdf_page", "image_sha256"}.issubset(payload):
            payload["_record_path"] = str(path.resolve())
            records.append(payload)
    return records


def _transcription_identity(settings: ProjectSettings, image_sha256: str, root: Path) -> str:
    prompt = root / "prompts" / "vision_transcription_v2.md"
    return stable_hash({
        "image_sha256": image_sha256,
        "prompt_sha256": sha256_file(prompt),
        "prompt_version": "vision_transcription_v2",
        "normalized_schema_version": "1.1",
        "provider": settings.vision_provider,
        "model": settings.vision_model,
        "base_url": settings.vision_base_url,
        "thinking_mode": settings.vision_thinking_mode,
        "max_output_tokens": settings.vision_max_output_tokens,
        "response_format_json_object": settings.vision_response_format_json_object,
    })


def import_matching_sample_vision_cache(
    settings: ProjectSettings,
    *,
    root: Path | None = None,
    sample_manifests: list[str | Path] | None = None,
) -> FullbookVisionCacheImportResult:
    """Reuse only byte-identical rendered sample images, without a network call.

    The preserved provider response is copied byte-for-byte.  A new derived
    normalized record rebinds document/page identity and explicitly records the
    cross-document provenance.  The original raw and normalized files are never
    modified.
    """

    root = (root or project_root()).resolve()
    manifests = sample_manifests or [
        root / "data" / "phase3b_source_sample12" / "manifests" / "sample_12_pages.manifest.json"
    ]
    candidates: dict[str, dict[str, Any]] = {}
    skipped = 0
    for manifest_path in manifests:
        path = Path(manifest_path)
        if not path.is_file():
            continue
        for item in load_json(path).get("pages", []):
            image_hash = item.get("image_sha256")
            normalized_path = item.get("normalized_visual_path")
            if image_hash and normalized_path and Path(normalized_path).is_file():
                candidates[str(image_hash)] = dict(item)

    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    imported: list[int] = []
    normalized_paths: list[str] = []
    for record in _fullbook_page_records(settings, root):
        page = int(record["pdf_page"])
        sample = candidates.get(str(record["image_sha256"]))
        if sample is None:
            skipped += 1
            continue
        source_normalized_path = Path(sample["normalized_visual_path"]).resolve()
        source_normalized = load_json(source_normalized_path)
        source_raw_path = Path(source_normalized["raw_response_path"]).resolve()
        if not source_raw_path.is_file():
            skipped += 1
            continue
        fingerprint = _transcription_identity(settings, str(record["image_sha256"]), root)
        raw_path = output / "raw" / f"{fingerprint}.json"
        normalized_path = output / "normalized" / f"page_{page:04d}_{fingerprint}.json"
        cache_path = output / "cache" / f"page_{page:04d}.json"
        if not raw_path.is_file():
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_raw_path, raw_path)
        rebound = dict(source_normalized)
        events = list(rebound.get("normalization_events") or [])
        events.append({
            "field": "document_id,pdf_page",
            "action": "reused_identical_rendered_image",
            "reason": "Rendered image SHA-256 and transcription identity matched an existing sample result.",
            "original_type": "object",
            "original_value": {
                "document_id": source_normalized.get("document_id"),
                "pdf_page": source_normalized.get("pdf_page"),
                "normalized_path": str(source_normalized_path),
            },
            "normalized_value": {
                "document_id": record["document_id"], "pdf_page": page,
            },
            "requires_review": False,
        })
        rebound.update({
            "document_id": record["document_id"],
            "pdf_page": page,
            "raw_response_path": str(raw_path.resolve()),
            "raw_response_sha256": sha256_file(raw_path),
            "previous_normalized_output_path": str(source_normalized_path),
            "previous_normalized_sha256": sha256_file(source_normalized_path),
            "normalized_at": _now(),
            "normalization_events": events,
            # This field describes the current production run, not historical provenance.
            "api_called": False,
            "translation_ready": False,
        })
        atomic_write_json(normalized_path, rebound)
        atomic_write_json(cache_path, {
            "status": "completed",
            "request_fingerprint": fingerprint,
            "document_id": record["document_id"],
            "pdf_page": page,
            "image_sha256": record["image_sha256"],
            "raw_response_path": str(raw_path.resolve()),
            "normalized_output_path": str(normalized_path.resolve()),
            "cache_origin": "identical_sample_render",
            "source_normalized_path": str(source_normalized_path),
            "api_called_this_run": False,
            "created_at": _now(),
        })
        imported.append(page)
        normalized_paths.append(str(normalized_path.resolve()))

    return FullbookVisionCacheImportResult(
        imported_pages=sorted(imported),
        skipped_nonmatching_pages=skipped,
        normalized_paths=normalized_paths,
        api_calls_this_run=0,
    )


def _load_fullbook_vision_ledger(path: Path, source_hash: str) -> dict[str, Any]:
    if path.is_file():
        ledger = load_json(path)
        if ledger.get("source_pdf_sha256") != source_hash:
            raise RuntimeError("source PDF hash changed since vision ledger creation")
        return ledger
    return {
        "schema_version": "fullbook-vision-ledger-1.0",
        "source_pdf_sha256": source_hash,
        "real_calls_started": 0,
        "attempts": [],
        "created_at": _now(),
        "updated_at": _now(),
    }


def recover_fullbook_vision_normalization(
    settings: ProjectSettings,
    *,
    root: Path | None = None,
) -> FullbookVisionRecoveryResult:
    """Re-normalize preserved successful provider responses without network access."""

    root = (root or project_root()).resolve()
    records = {int(item["pdf_page"]): item for item in _fullbook_page_records(settings, root)}
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    ledger_path = output / "call_ledger.json"
    recovered: list[int] = []
    failed: list[int] = []
    normalized_paths: list[str] = []
    if not ledger_path.is_file():
        return FullbookVisionRecoveryResult(
            recovered_pages=[], still_failed_pages=[], normalized_paths=[], api_calls_this_run=0
        )
    source_hash = str(next(iter(records.values()))["source_pdf_sha256"]) if records else ""
    ledger = _load_fullbook_vision_ledger(ledger_path, source_hash)
    checkpoint = FullbookCheckpointStore(
        resolve_project_path(settings.fullbook_checkpoint_path, root=root),
        source_pdf_sha256=source_hash,
    )
    for error_path in sorted((output / "errors").glob("page_*.json")):
        error = load_json(error_path)
        page = int(error.get("pdf_page", 0))
        fingerprint = str(error.get("request_fingerprint", ""))
        if page not in records or not fingerprint:
            continue
        raw_path = output / "raw" / f"{fingerprint}.json"
        normalized_path = output / "normalized" / f"page_{page:04d}_{fingerprint}.json"
        cache_path = output / "cache" / f"page_{page:04d}.json"
        usage_path = output / "usage" / f"page_{page:04d}_{fingerprint}.json"
        recovery_path = output / "recovery" / f"page_{page:04d}_{fingerprint}.json"
        if cache_path.is_file():
            existing = load_json(cache_path)
            existing_normalized = Path(str(existing.get("normalized_output_path", "")))
            if existing.get("status") == "completed" and existing_normalized.is_file():
                continue
        if not raw_path.is_file():
            failed.append(page)
            continue
        raw_hash = sha256_file(raw_path)
        try:
            normalized = normalize_preserved_response_v11(raw_path, normalized_path)
            raw = load_json(raw_path)
            response = raw.get("response") or {}
            usage = response.get("usage") or {}
            atomic_write_json(usage_path, {
                "request_fingerprint": fingerprint,
                "request_id": response.get("id"),
                "usage": usage,
                "recovered_offline": True,
                "cash_charge_confirmed": False,
                "cash_charge_cny": None,
            })
            atomic_write_json(cache_path, {
                "status": "completed", "request_fingerprint": fingerprint,
                "document_id": records[page]["document_id"], "pdf_page": page,
                "image_sha256": records[page]["image_sha256"],
                "raw_response_path": str(raw_path.resolve()),
                "normalized_output_path": str(normalized_path.resolve()),
                "usage_path": str(usage_path.resolve()),
                "api_called_this_run": False,
                "normalized_status": normalized.status,
                "cache_origin": "offline_normalization_recovery",
                "created_at": _now(),
            })
            atomic_write_json(recovery_path, {
                "pdf_page": page, "request_fingerprint": fingerprint,
                "original_error_path": str(error_path.resolve()),
                "raw_response_sha256": raw_hash,
                "normalized_output_path": str(normalized_path.resolve()),
                "api_calls_this_run": 0, "status": "recovered", "recovered_at": _now(),
            })
            for attempt in ledger.get("attempts", []):
                if attempt.get("pdf_page") == page and attempt.get("request_fingerprint") == fingerprint:
                    attempt["status"] = "completed_after_offline_normalization"
                    attempt["offline_recovered_at"] = _now()
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            checkpoint.mark_completed("vision_single", f"page_{page:04d}")
            checkpoint.clear_quarantine("vision_single", f"page_{page:04d}")
            if sha256_file(raw_path) != raw_hash:
                raise RuntimeError("preserved raw response changed during offline recovery")
            recovered.append(page)
            normalized_paths.append(str(normalized_path.resolve()))
        except Exception:
            failed.append(page)
    return FullbookVisionRecoveryResult(
        recovered_pages=sorted(set(recovered)),
        still_failed_pages=sorted(set(failed) - set(recovered)),
        normalized_paths=normalized_paths,
        api_calls_this_run=0,
    )


def run_fullbook_vision_batch(
    settings: ProjectSettings,
    *,
    pages: list[int],
    root: Path | None = None,
    allow_api: bool = False,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
    api_key_loader: Callable[..., tuple[str, str]] = load_api_key,
) -> FullbookVisionBatchResult:
    """Transcribe a bounded page batch with durable per-page cache and no retries."""

    root = (root or project_root()).resolve()
    requested = sorted(set(pages))
    records = {int(item["pdf_page"]): item for item in _fullbook_page_records(settings, root)}
    missing = [page for page in requested if page not in records]
    if missing:
        raise FileNotFoundError(f"Rendered page records missing: {missing}")
    source_hashes = {str(records[page]["source_pdf_sha256"]) for page in requested}
    if len(source_hashes) != 1:
        raise RuntimeError("full-book page records do not share one source PDF hash")
    source_hash = next(iter(source_hashes))
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    ledger_path = output / "call_ledger.json"
    ledger = _load_fullbook_vision_ledger(ledger_path, source_hash)
    checkpoint = FullbookCheckpointStore(
        resolve_project_path(settings.fullbook_checkpoint_path, root=root),
        source_pdf_sha256=source_hash,
    )
    prompt_path = root / "prompts" / "vision_transcription_v2.md"
    prompt = prompt_path.read_text(encoding="utf-8")

    pending: list[tuple[int, dict[str, Any], str]] = []
    cached: list[int] = []
    normalized_paths: list[str] = []
    raw_paths: list[str] = []
    for page in requested:
        record = records[page]
        fingerprint = _transcription_identity(settings, str(record["image_sha256"]), root)
        cache_path = output / "cache" / f"page_{page:04d}.json"
        if cache_path.is_file():
            cache = load_json(cache_path)
            final_state = Path(str(cache.get("final_state_path", "")))
            if (
                cache.get("status") == "final_blank"
                and cache.get("image_sha256") == record["image_sha256"]
                and final_state.is_file()
            ):
                cached.append(page)
                normalized_paths.append(str(final_state.resolve()))
                checkpoint.mark_completed("vision_single", f"page_{page:04d}")
                continue
            normalized = Path(str(cache.get("normalized_output_path", "")))
            raw = Path(str(cache.get("raw_response_path", "")))
            if (
                cache.get("status") == "completed"
                and cache.get("request_fingerprint") == fingerprint
                and cache.get("image_sha256") == record["image_sha256"]
                and normalized.is_file()
                and raw.is_file()
            ):
                cached.append(page)
                normalized_paths.append(str(normalized.resolve()))
                raw_paths.append(str(raw.resolve()))
                checkpoint.mark_completed("vision_single", f"page_{page:04d}")
                continue
        pending.append((page, record, fingerprint))

    if pending and not allow_api:
        raise PermissionError("real vision API is disabled for pending full-book pages")
    key = ""
    key_name = ""
    client = None
    if pending:
        key, key_name = api_key_loader(settings, root)
        client = provider_factory(
            api_key=key,
            base_url=settings.vision_base_url,
            timeout_seconds=settings.vision_request_timeout_seconds,
        )

    completed: list[int] = []
    failed: list[int] = []
    api_calls = 0
    total_tokens = 0
    consecutive_signature: str | None = None
    consecutive_count = 0
    started = time.perf_counter()
    for page, record, fingerprint in pending:
        raw_path = output / "raw" / f"{fingerprint}.json"
        normalized_path = output / "normalized" / f"page_{page:04d}_{fingerprint}.json"
        usage_path = output / "usage" / f"page_{page:04d}_{fingerprint}.json"
        request_path = output / "request" / f"page_{page:04d}_{fingerprint}.json"
        cache_path = output / "cache" / f"page_{page:04d}.json"
        error_path = output / "errors" / f"page_{page:04d}_{fingerprint}.json"
        attempt = {
            "pdf_page": page,
            "request_fingerprint": fingerprint,
            "status": "in_flight",
            "automatic_retry": False,
            "started_at": _now(),
        }
        ledger["real_calls_started"] = int(ledger.get("real_calls_started", 0)) + 1
        ledger.setdefault("attempts", []).append(attempt)
        ledger["updated_at"] = _now()
        atomic_write_json(ledger_path, ledger)
        checkpoint.increment_api_call("single")
        api_calls += 1
        request_metadata = {
            "schema_version": "fullbook-vision-request-1.0",
            "phase": "6",
            "status": "in_flight",
            "request_fingerprint": fingerprint,
            "provider": settings.vision_provider,
            "model": settings.vision_model,
            "base_url": settings.vision_base_url,
            "api_key_env": key_name,
            "api_key_recorded": False,
            "pdf_page": page,
            "document_id": record["document_id"],
            "source_pdf_sha256": source_hash,
            "image_path": record["image_path"],
            "image_sha256": record["image_sha256"],
            "prompt_path": str(prompt_path.resolve()),
            "prompt_sha256": sha256_file(prompt_path),
            "automatic_retry": False,
            "started_at": attempt["started_at"],
        }
        atomic_write_json(request_path, request_metadata)
        try:
            image_data = "data:image/png;base64," + base64.b64encode(
                Path(record["image_path"]).read_bytes()
            ).decode("ascii")
            context = (
                f"Technical context only: document_id={record['document_id']}; "
                f"pdf_page={page}; provider={settings.vision_provider}; "
                f"model={settings.vision_model}; schema_version=2.0. Use only the attached image."
            )
            response = client.transcribe_one_page(
                model=settings.vision_model,
                prompt=prompt,
                context_message=context,
                image_data_url=image_data,
                max_output_tokens=settings.vision_max_output_tokens,
                temperature=settings.vision_temperature,
                do_sample=settings.vision_do_sample,
                thinking_mode=settings.vision_thinking_mode,
                response_format_json_object=settings.vision_response_format_json_object,
            )
            atomic_write_json(raw_path, {
                "record_type": "raw_provider_response",
                "request_fingerprint": fingerprint,
                "provider": settings.vision_provider,
                "model": settings.vision_model,
                "api_called": True,
                "received_at": _now(),
                "response": response.raw_response,
            })
            normalized = normalize_preserved_response_v11(raw_path, normalized_path)
            usage = response.usage or {}
            total_tokens += int(usage.get("total_tokens") or 0)
            atomic_write_json(usage_path, {
                "request_fingerprint": fingerprint,
                "request_id": response.request_id,
                "usage": usage,
                "cash_charge_confirmed": False,
                "cash_charge_cny": None,
            })
            request_metadata.update({
                "status": "completed", "request_id": response.request_id,
                "ended_at": _now(), "raw_response_path": str(raw_path.resolve()),
                "normalized_output_path": str(normalized_path.resolve()), "retries": 0,
            })
            atomic_write_json(request_path, request_metadata)
            attempt.update({"status": "completed", "ended_at": _now(), "request_id": response.request_id})
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            atomic_write_json(cache_path, {
                "status": "completed", "request_fingerprint": fingerprint,
                "document_id": record["document_id"], "pdf_page": page,
                "image_sha256": record["image_sha256"],
                "raw_response_path": str(raw_path.resolve()),
                "normalized_output_path": str(normalized_path.resolve()),
                "usage_path": str(usage_path.resolve()), "api_called_this_run": True,
                "normalized_status": normalized.status, "created_at": _now(),
            })
            checkpoint.mark_completed("vision_single", f"page_{page:04d}")
            completed.append(page)
            raw_paths.append(str(raw_path.resolve()))
            normalized_paths.append(str(normalized_path.resolve()))
            consecutive_signature = None
            consecutive_count = 0
        except Exception as exc:
            safe_message = str(exc).replace(key, "[REDACTED]")[:1000]
            signature = type(exc).__name__
            if signature == consecutive_signature:
                consecutive_count += 1
            else:
                consecutive_signature = signature
                consecutive_count = 1
            failed.append(page)
            attempt.update({"status": "failed", "ended_at": _now(), "error_type": signature})
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            atomic_write_json(error_path, {
                "record_type": "provider_or_normalization_error",
                "request_fingerprint": fingerprint, "pdf_page": page,
                "error_type": signature, "error_message": safe_message,
                "api_called": True, "automatic_retry": False, "recorded_at": _now(),
            })
            checkpoint.mark_quarantine("vision_single", f"page_{page:04d}", safe_message)
            if consecutive_count >= 3:
                raise RuntimeError(
                    f"three consecutive {signature} failures; full-book vision stopped"
                ) from exc

    return FullbookVisionBatchResult(
        requested_pages=requested,
        completed_pages=completed,
        cached_pages=cached,
        failed_pages=failed,
        raw_paths=raw_paths,
        normalized_paths=normalized_paths,
        api_calls_this_run=api_calls,
        total_tokens_this_run=total_tokens,
        elapsed_seconds=round(time.perf_counter() - started, 3),
    )


def _fullbook_normalized_pages(
    settings: ProjectSettings, root: Path, page_count: int
) -> dict[int, tuple[VisionNormalizedPageV11, Path]]:
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "vision"
    pages: dict[int, tuple[VisionNormalizedPageV11, Path]] = {}
    for page in range(1, page_count + 1):
        cache_path = output / "cache" / f"page_{page:04d}.json"
        if not cache_path.is_file():
            continue
        cache = load_json(cache_path)
        path = Path(str(cache.get("normalized_output_path", "")))
        if not path.is_file():
            continue
        item = VisionNormalizedPageV11.model_validate(load_json(path))
        if item.pdf_page != page:
            raise RuntimeError(f"normalized page identity mismatch at page {page}")
        pages[page] = (item, path)
    return pages


def select_fullbook_pair_candidates(
    boundaries: list[AutomatedBoundary],
) -> list[AutomatedBoundary]:
    """Return only unresolved internal page boundaries, in reading order."""

    return sorted(
        [
            item for item in boundaries
            if item.auto_resolution_status == "unresolved"
            and item.next_page_available
            and item.next_fragment_id is not None
            and not item.previous_fragment_id.startswith("fragment_missing_")
            and not item.next_fragment_id.startswith("fragment_missing_")
        ],
        key=lambda item: (item.previous_page, item.next_page),
    )


def run_fullbook_pair_batch(
    settings: ProjectSettings,
    boundaries: list[AutomatedBoundary],
    *,
    root: Path | None = None,
    allow_api: bool = False,
    provider_factory: Callable[..., Any] = ZhipuOpenAICompatibleProvider,
    api_key_loader: Callable[..., tuple[str, str]] = load_api_key,
) -> FullbookBoundaryBatchResult:
    """Review only unresolved adjacent boundaries with two page images."""

    root = (root or project_root()).resolve()
    candidates = select_fullbook_pair_candidates(boundaries)
    page_records = {int(item["pdf_page"]): item for item in _fullbook_page_records(settings, root)}
    page_count = max(page_records, default=0)
    normalized = _fullbook_normalized_pages(settings, root, page_count)
    if any(item.previous_page not in normalized or item.next_page not in normalized for item in candidates):
        raise RuntimeError("pair review requires both normalized adjacent pages")
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "boundaries" / "pair"
    prompt_path = resolve_project_path(settings.boundary_prompt_path, root=root)
    prompt = prompt_path.read_text(encoding="utf-8")
    completed: list[str] = []
    cached: list[str] = []
    unresolved: list[str] = []
    failed: list[str] = []
    paths_out: list[str] = []
    api_calls = 0
    total_tokens = 0
    pending: list[tuple[AutomatedBoundary, str, dict[str, Path]]] = []
    for boundary in candidates:
        left_item, left_path = normalized[boundary.previous_page]
        right_item, right_path = normalized[boundary.next_page]
        left_record, right_record = page_records[boundary.previous_page], page_records[boundary.next_page]
        fingerprint = stable_hash({
            "phase": "6", "category": "pair", "boundary_id": boundary.boundary_id,
            "provider": settings.vision_provider, "model": settings.vision_model,
            "base_url": settings.vision_base_url.rstrip("/"),
            "previous_image_sha256": left_record["image_sha256"],
            "next_image_sha256": right_record["image_sha256"],
            "previous_result_sha256": sha256_file(left_path),
            "next_result_sha256": sha256_file(right_path),
            "prompt_sha256": sha256_file(prompt_path),
            "boundary_schema_version": settings.boundary_schema_version,
            "max_output_tokens": settings.vision_max_output_tokens,
        })
        item_dir = output / boundary.boundary_id
        paths = {
            "raw": item_dir / f"{fingerprint}.raw.json",
            "normalized": item_dir / f"{fingerprint}.normalized.json",
            "validation": item_dir / f"{fingerprint}.validation.json",
            "request": item_dir / f"{fingerprint}.request.json",
            "usage": item_dir / f"{fingerprint}.usage.json",
            "cache": item_dir / "cache.json",
            "error": item_dir / f"{fingerprint}.error.json",
        }
        if paths["cache"].is_file():
            cache = load_json(paths["cache"])
            normalized_path = Path(str(cache.get("normalized_output_path", "")))
            raw_path = Path(str(cache.get("raw_response_path", "")))
            if cache.get("request_fingerprint") == fingerprint and normalized_path.is_file() and raw_path.is_file():
                decision = BoundaryDecision.model_validate(load_json(normalized_path))
                cached.append(boundary.boundary_id)
                paths_out.append(str(normalized_path.resolve()))
                if decision.status == "needs_review":
                    unresolved.append(boundary.boundary_id)
                continue
        pending.append((boundary, fingerprint, paths))
    if pending and not allow_api:
        raise PermissionError("real pair API is disabled for pending full-book boundaries")
    key = key_name = ""
    client = None
    if pending:
        key, key_name = api_key_loader(settings, root)
        client = provider_factory(
            api_key=key, base_url=settings.vision_base_url,
            timeout_seconds=settings.vision_request_timeout_seconds,
        )
    checkpoint_path = resolve_project_path(settings.fullbook_checkpoint_path, root=root)
    source_hash = str(next(iter(page_records.values()))["source_pdf_sha256"]) if page_records else ""
    checkpoint = FullbookCheckpointStore(checkpoint_path, source_pdf_sha256=source_hash)
    ledger_path = output.parent / "boundary_call_ledger.json"
    ledger = _load_fullbook_vision_ledger(ledger_path, source_hash)
    consecutive_signature: str | None = None
    consecutive_count = 0
    for boundary, fingerprint, paths in pending:
        left_item, _ = normalized[boundary.previous_page]
        right_item, _ = normalized[boundary.next_page]
        left_record, right_record = page_records[boundary.previous_page], page_records[boundary.next_page]
        attempt = {
            "category": "pair", "boundary_id": boundary.boundary_id,
            "request_fingerprint": fingerprint, "status": "in_flight",
            "automatic_retry": False, "started_at": _now(),
        }
        ledger["real_calls_started"] = int(ledger.get("real_calls_started", 0)) + 1
        ledger.setdefault("attempts", []).append(attempt)
        ledger["updated_at"] = _now()
        atomic_write_json(ledger_path, ledger)
        checkpoint.increment_api_call("pair")
        api_calls += 1
        metadata = {
            "schema_version": "fullbook-boundary-request-1.0", "phase": "6",
            "category": "pair", "boundary_id": boundary.boundary_id,
            "request_fingerprint": fingerprint, "status": "in_flight",
            "provider": settings.vision_provider, "model": settings.vision_model,
            "base_url": settings.vision_base_url, "api_key_env": key_name,
            "api_key_recorded": False, "automatic_retry": False,
            "image_sha256": [left_record["image_sha256"], right_record["image_sha256"]],
            "started_at": _now(),
        }
        atomic_write_json(paths["request"], metadata)
        structured_context = {
            "boundary_id": boundary.boundary_id,
            "document_id": left_item.document_id,
            "previous_page": boundary.previous_page,
            "next_page": boundary.next_page,
            "previous_tail_text": boundary.previous_tail_text,
            "next_head_text": boundary.next_head_text,
            "previous_page_transcription": left_item.model_dump(mode="json"),
            "next_page_transcription": right_item.model_dump(mode="json"),
        }
        try:
            image_urls = [
                "data:image/png;base64," + base64.b64encode(Path(record["image_path"]).read_bytes()).decode("ascii")
                for record in (left_record, right_record)
            ]
            response = client.transcribe_images(
                model=settings.vision_model, prompt=prompt,
                context_message=json.dumps(structured_context, ensure_ascii=False),
                image_data_urls=image_urls, max_output_tokens=settings.vision_max_output_tokens,
                temperature=settings.vision_temperature, do_sample=settings.vision_do_sample,
                thinking_mode=settings.vision_thinking_mode,
                response_format_json_object=settings.vision_response_format_json_object,
            )
            atomic_write_json(paths["raw"], {
                "record_type": "raw_provider_response", "phase": "6", "category": "pair",
                "boundary_id": boundary.boundary_id, "request_fingerprint": fingerprint,
                "provider": settings.vision_provider, "model": settings.vision_model,
                "api_called": True, "received_at": _now(), "response": response.raw_response,
            })
            left_ns = SimpleNamespace(**left_record)
            right_ns = SimpleNamespace(**right_record)
            decision = _boundary_from_response(
                response, settings=settings, context=SimpleNamespace(document_id=left_item.document_id),
                previous_record=left_ns, next_record=right_ns,
                previous_result=left_item, next_result=right_item,
                review_window=[boundary.previous_page, boundary.next_page],
                raw_path=paths["raw"], normalized_path=paths["normalized"],
                validation_path=paths["validation"],
            )
            usage = response.usage or {}
            total_tokens += int(usage.get("total_tokens") or 0)
            atomic_write_json(paths["usage"], {
                "request_fingerprint": fingerprint, "request_id": response.request_id,
                "usage": usage, "cash_charge_confirmed": False, "cash_charge_cny": None,
            })
            metadata.update({"status": "completed", "ended_at": _now(), "retries": 0})
            atomic_write_json(paths["request"], metadata)
            attempt.update({"status": "completed", "ended_at": _now()})
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            atomic_write_json(paths["cache"], {
                "request_fingerprint": fingerprint, "status": decision.status,
                "raw_response_path": str(paths["raw"].resolve()),
                "normalized_output_path": str(paths["normalized"].resolve()),
                "usage_path": str(paths["usage"].resolve()), "api_called_this_run": True,
            })
            checkpoint.mark_completed("boundary_pair", boundary.boundary_id)
            completed.append(boundary.boundary_id)
            paths_out.append(str(paths["normalized"].resolve()))
            if decision.status == "needs_review":
                unresolved.append(boundary.boundary_id)
            consecutive_signature = None
            consecutive_count = 0
        except Exception as exc:
            signature = type(exc).__name__
            safe = str(exc).replace(key, "[REDACTED]")[:1000]
            consecutive_count = consecutive_count + 1 if signature == consecutive_signature else 1
            consecutive_signature = signature
            failed.append(boundary.boundary_id)
            attempt.update({"status": "failed", "ended_at": _now(), "error_type": signature})
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            atomic_write_json(paths["error"], {
                "boundary_id": boundary.boundary_id, "request_fingerprint": fingerprint,
                "error_type": signature, "error_message": safe, "api_called": True,
                "automatic_retry": False, "recorded_at": _now(),
            })
            checkpoint.mark_quarantine("boundary_pair", boundary.boundary_id, safe)
            if consecutive_count >= 3:
                raise RuntimeError(f"three consecutive {signature} pair failures; stopped") from exc
    return FullbookBoundaryBatchResult(
        category="pair", requested_boundaries=[item.boundary_id for item in candidates],
        completed_boundaries=completed, cached_boundaries=cached,
        unresolved_boundaries=sorted(set(unresolved)), failed_boundaries=failed,
        normalized_paths=paths_out, api_calls_this_run=api_calls,
        total_tokens_this_run=total_tokens,
    )


def _original_block_labels(item: VisionNormalizedPageV11) -> dict[str, str]:
    labels: dict[str, str] = {}
    for event in item.normalization_events:
        match = re.fullmatch(r"blocks\[(\d+)\]\.block_type", event.field)
        if match and event.action == "unsupported_label_to_unknown" and isinstance(event.original_value, str):
            index = int(match.group(1))
            if 0 <= index < len(item.blocks):
                labels[item.blocks[index].block_id] = event.original_value
    return labels


def build_fullbook_automated_pages(
    settings: ProjectSettings,
    preflight: FullbookPreflight,
    *,
    root: Path | None = None,
    output_directory: Path | None = None,
    artifact_name: str = "fullbook.pages.jsonl",
) -> FullbookAutomatedPageBuildResult:
    """Adapt full-book caches to the verified automated-page schema and rules."""

    root = (root or project_root()).resolve()
    source = Path(preflight.source_pdf)
    if sha256_file(source) != preflight.source_pdf_sha256:
        raise RuntimeError("source PDF hash changed before automated page construction")
    normalized = _fullbook_normalized_pages(settings, root, preflight.actual_page_count)
    from .main_text_edition import effective_translatable_block_type, verified_blank_state

    verified_blanks = {
        page
        for page in range(1, preflight.actual_page_count + 1)
        if verified_blank_state(root, page) is not None
    }
    missing = sorted(
        set(range(1, preflight.actual_page_count + 1)) - set(normalized) - verified_blanks
    )
    records = {int(item["pdf_page"]): item for item in _fullbook_page_records(settings, root)}
    bodies: dict[int, list[Any]] = {}
    selected_by_page: dict[int, list[tuple[Any, str]]] = {}
    for page in range(1, preflight.actual_page_count + 1):
        if page not in normalized:
            selected_by_page[page] = []
            bodies[page] = []
            continue
        item, _ = normalized[page]
        original = _original_block_labels(item)
        selected: list[tuple[Any, str]] = []
        for block in sorted(item.blocks, key=lambda value: value.order):
            original_label = original.get(block.block_id)
            effective = effective_translatable_block_type(
                block.block_type, original_label, block.text, item.page_type
            )
            if effective is None:
                continue
            selected.append((block, effective))
        selected_by_page[page] = selected
        bodies[page] = [block for block, effective in selected if effective == "body"]

    external: dict[tuple[int, int], str] = {}
    body_pages = [page for page in range(1, preflight.actual_page_count + 1) if bodies[page]]
    for page, next_page in zip(body_pages, body_pages[1:]):
        chapter = any(effective == "chapter_title" for _, effective in selected_by_page[next_page])
        external[(page, next_page)] = _edge_relation(
            bodies[page][-1].text, bodies[next_page][0].text, chapter
        )
    incoming_relation = {right: relation for (left, right), relation in external.items()}
    outgoing_relation = {left: relation for (left, right), relation in external.items()}

    pages: list[AutomatedPageRecord] = []
    partial: list[int] = []
    with fitz.open(source) as document:
        for page in range(1, preflight.actual_page_count + 1):
            if page in verified_blanks:
                record = records[page]
                state = verified_blank_state(root, page)
                pages.append(AutomatedPageRecord(
                    schema_version=settings.automated_page_schema_version,
                    document_id=record["document_id"], pdf_page=page, printed_page=None,
                    page_type="blank", full_visible_text="", complete_blocks=[],
                    head_fragment=None, tail_fragment=None, content_fragments=[],
                    running_header=None, footer=None, page_number_text=None, titles=[],
                    image_sha256=record["image_sha256"], text_layer_text="",
                    text_layer_similarity=1.0,
                    transcription_status=str(state["transcription_status"]),
                    source_coverage_status="complete", legacy_normalized_path="",
                    created_at=datetime.now(timezone.utc),
                ))
                continue
            if page not in normalized:
                record = records[page]
                missing_fragment = SourceFragment(
                    fragment_id="fragment_missing_" + stable_hash({
                        "document_id": record["document_id"], "page": page,
                        "reason": "missing_visual_transcription",
                    })[:20],
                    text="", source_page=page,
                    source_block_ids=[f"p{page:04d}:missing_visual_transcription"],
                    block_type="body", order=1,
                    starts_mid_sentence=True, ends_mid_sentence=True,
                    starts_mid_paragraph=True, ends_mid_paragraph=True,
                    visible_trailing_hyphen=False,
                    uncertainty=["missing_visual_transcription"],
                )
                pages.append(AutomatedPageRecord(
                    schema_version=settings.automated_page_schema_version,
                    document_id=record["document_id"], pdf_page=page, printed_page=None,
                    page_type="unknown", full_visible_text="", complete_blocks=[],
                    head_fragment=missing_fragment, tail_fragment=missing_fragment,
                    content_fragments=[missing_fragment], running_header=None, footer=None,
                    page_number_text=None, titles=[], image_sha256=record["image_sha256"],
                    text_layer_text="", text_layer_similarity=0.0,
                    transcription_status="failed_missing_visual_transcription",
                    source_coverage_status="failed", legacy_normalized_path="",
                    created_at=datetime.now(timezone.utc),
                ))
                partial.append(page)
                continue
            item, normalized_path = normalized[page]
            selected = selected_by_page[page]
            body = bodies[page]
            first_body_id = body[0].block_id if body else None
            last_body_id = body[-1].block_id if body else None
            fragments: list[SourceFragment] = []
            previous_body: Any | None = None
            for block, effective_type in selected:
                is_body = effective_type == "body"
                body_index = body.index(block) if is_body else -1
                next_body = body[body_index + 1] if is_body and body_index + 1 < len(body) else None
                starts_external = bool(
                    is_body and block.block_id == first_body_id and page > 1
                    and incoming_relation.get(page) in {"continue", "unresolved"}
                )
                starts_internal = bool(
                    is_body and previous_body is not None
                    and _within_page_continuation(previous_body.text, block.text)
                )
                ends_internal = bool(
                    is_body and next_body is not None
                    and _within_page_continuation(block.text, next_body.text)
                )
                ends_external = bool(
                    is_body and block.block_id == last_body_id and page < preflight.actual_page_count
                    and outgoing_relation.get(page) in {"continue", "unresolved"}
                )
                fragments.append(SourceFragment(
                    fragment_id=_fragment_id(item.document_id, page, block.block_id, block.text),
                    text=block.text, source_page=page,
                    source_block_ids=[f"p{page:04d}:{block.block_id}"],
                    block_type=effective_type, order=block.order,
                    starts_mid_sentence=starts_external or starts_internal,
                    ends_mid_sentence=ends_external or ends_internal,
                    starts_mid_paragraph=starts_external or starts_internal,
                    ends_mid_paragraph=ends_external or ends_internal,
                    visible_trailing_hyphen=block.text.rstrip().endswith("-"),
                    uncertainty=list(item.uncertain_characters) if block.uncertain else [],
                ))
                if is_body:
                    previous_body = block
            by_block = {fragment.source_block_ids[0].split(":", 1)[1]: fragment for fragment in fragments}
            head = by_block.get(first_body_id) if first_body_id and page > 1 and incoming_relation.get(page) in {"continue", "unresolved"} else None
            tail = by_block.get(last_body_id) if last_body_id and page < preflight.actual_page_count and outgoing_relation.get(page) in {"continue", "unresolved"} else None
            titles = [fragment.text for fragment in fragments if fragment.block_type in {"chapter_title", "section_title", "other_translatable"}]
            visible_parts: list[str] = []
            for value in [item.running_header, item.title] + [block.text for block in item.blocks] + [item.footer, item.page_number_text]:
                if value and value not in visible_parts:
                    visible_parts.append(value)
            full_visible = "\n\n".join(visible_parts)
            text_layer = document.load_page(page - 1).get_text("text")
            ratio = _similarity(full_visible, text_layer)
            coverage = "complete" if ratio >= settings.automated_text_layer_coverage_threshold else "partial"
            if coverage != "complete":
                partial.append(page)
            record = records[page]
            pages.append(AutomatedPageRecord(
                schema_version=settings.automated_page_schema_version,
                document_id=item.document_id, pdf_page=page, printed_page=item.printed_page,
                page_type=item.page_type, full_visible_text=full_visible,
                complete_blocks=[fragment.fragment_id for fragment in fragments if not (
                    fragment.starts_mid_sentence or fragment.ends_mid_sentence
                    or fragment.starts_mid_paragraph or fragment.ends_mid_paragraph
                )],
                head_fragment=head, tail_fragment=tail, content_fragments=fragments,
                running_header=item.running_header, footer=item.footer,
                page_number_text=item.page_number_text, titles=titles,
                image_sha256=record["image_sha256"], text_layer_text=text_layer,
                text_layer_similarity=ratio, transcription_status=item.status,
                source_coverage_status=coverage, legacy_normalized_path=str(normalized_path),
                created_at=datetime.now(timezone.utc),
            ))
    output_root = (
        output_directory.resolve()
        if output_directory is not None
        else resolve_project_path(settings.fullbook_data_directory, root=root) / "automated_pages"
    )
    output = output_root / artifact_name
    from .io_utils import atomic_write_jsonl
    atomic_write_jsonl(output, pages)
    for page in pages:
        atomic_write_json(output.parent / "pages" / f"page_{page.pdf_page:04d}.json", page)
    return FullbookAutomatedPageBuildResult(
        page_count=len(pages), fragment_count=sum(len(page.content_fragments) for page in pages),
        partial_coverage_pages=partial, output_path=str(output.resolve()),
    )


def build_fullbook_translation_units(
    logical_blocks: list[AutomatedLogicalBlock],
) -> list[Phase3C4TranslationUnit]:
    """Build target-only translation units for all complete translatable blocks."""

    eligible = [
        item for item in logical_blocks
        if item.translation_ready and item.block_type in TRANSLATABLE_TYPES
        and not item.unresolved_boundaries and item.source_text.strip()
    ]
    by_id = {item.logical_block_id: item for item in logical_blocks}
    bodies_by_chapter: dict[str | None, list[AutomatedLogicalBlock]] = {}
    for item in eligible:
        if item.block_type == "body":
            bodies_by_chapter.setdefault(item.chapter_id, []).append(item)
    units: list[Phase3C4TranslationUnit] = []
    for block in eligible:
        before = after = None
        if block.block_type == "body":
            peers = bodies_by_chapter.get(block.chapter_id, [])
            index = peers.index(block)
            before = peers[index - 1] if index else None
            after = peers[index + 1] if index + 1 < len(peers) else None
        chapter = by_id.get(block.chapter_id) if block.chapter_id and block.chapter_id != block.logical_block_id else None
        section = by_id.get(block.section_id) if block.section_id and block.section_id != block.logical_block_id else None
        if chapter and chapter.block_type not in TITLE_TYPES:
            chapter = None
        if section and section.block_type not in TITLE_TYPES:
            section = None
        units.append(Phase3C4TranslationUnit(
            target_block_id=block.logical_block_id, block_type=block.block_type,
            source_text=block.source_text, source_pages=block.source_pages,
            source_fragment_ids=block.source_fragment_ids, completeness_status="complete",
            chapter_id=block.chapter_id, section_id=block.section_id,
            chapter_title_block_id=chapter.logical_block_id if chapter else None,
            section_title_block_id=section.logical_block_id if section else None,
            chapter_title_context=chapter.source_text if chapter else None,
            section_title_context=section.source_text if section else None,
            context_before_block_ids=[before.logical_block_id] if before else [],
            context_after_block_ids=[after.logical_block_id] if after else [],
            context_before_text=before.source_text if before else None,
            context_after_text=after.source_text if after else None,
            translate_target_only=True, translation_ready=True,
        ))
    return units


def run_fullbook_translation_batch(
    settings: ProjectSettings,
    units: list[Phase3C4TranslationUnit],
    *,
    root: Path | None = None,
    allow_api: bool = False,
    provider_factory: Callable[..., Any] = DeepSeekOpenAICompatibleProvider,
    api_key_loader: Callable[..., tuple[str, str]] = load_translation_api_key,
) -> FullbookTranslationBatchResult:
    """Translate complete units one at a time with durable cache and no retries."""

    root = (root or project_root()).resolve()
    output = resolve_project_path(settings.fullbook_data_directory, root=root) / "translations"
    prompt_path = resolve_project_path(settings.phase3c4_prompt_path, root=root)
    prompt = prompt_path.read_text(encoding="utf-8")
    profile = _profile_text(settings, root)
    prompt_hash, profile_hash = sha256_text(prompt), sha256_text(profile)
    results: list[Phase3C4TranslationResult] = []
    request_paths: list[str] = []
    pending: list[tuple[Phase3C4TranslationUnit, str, dict[str, Path]]] = []
    for unit in units:
        fingerprint = stable_hash({
            "phase": "6", "unit": unit.model_dump(mode="json"),
            "prompt_version": settings.phase3c4_prompt_version,
            "prompt_sha256": prompt_hash,
            "profile_version": settings.translation_language_profile_version,
            "profile_sha256": profile_hash,
            "provider": settings.translation_provider,
            "base_url": settings.translation_base_url,
            "model": settings.translation_model,
            "thinking_mode": settings.translation_thinking_mode,
            "temperature": settings.translation_temperature,
            "max_output_tokens": settings.translation_max_output_tokens,
            "response_format": "json_object",
        })
        folder = output / "calls" / fingerprint
        paths = {name: folder / f"{name}.json" for name in (
            "request", "raw", "normalized", "usage", "cache", "error"
        )}
        if paths["cache"].is_file():
            try:
                cached = Phase3C4TranslationResult.model_validate(load_json(paths["cache"]))
                if cached.request_fingerprint == fingerprint:
                    results.append(cached)
                    request_paths.append(str(paths["request"].resolve()))
                    continue
            except Exception:
                pass
        pending.append((unit, fingerprint, paths))
    cache_hits = len(results)
    if pending and not allow_api:
        return FullbookTranslationBatchResult(
            results=results, api_calls_this_run=0, cache_hits=cache_hits,
            failed_block_ids=[], pending_block_ids=[item[0].target_block_id for item in pending],
            request_paths=request_paths, total_tokens_this_run=0,
        )
    key = key_name = ""
    provider = None
    if pending:
        key, key_name = api_key_loader(settings, root)
        provider = provider_factory(
            api_key=key, base_url=settings.translation_base_url,
            timeout_seconds=settings.translation_request_timeout_seconds,
        )
    ledger_path = output / "call_ledger.json"
    if ledger_path.is_file():
        ledger = load_json(ledger_path)
    else:
        ledger = {
            "schema_version": "fullbook-translation-ledger-1.0",
            "content_calls_started": 0, "entries": [], "created_at": _now(),
        }
    calls = 0
    tokens = 0
    failed: list[str] = []
    consecutive_signature: str | None = None
    consecutive_count = 0
    checkpoint_path = resolve_project_path(settings.fullbook_checkpoint_path, root=root)
    source_hash = load_json(checkpoint_path).get("source_pdf_sha256") if checkpoint_path.is_file() else "test-source"
    checkpoint = FullbookCheckpointStore(checkpoint_path, source_pdf_sha256=str(source_hash))
    system_prompt = prompt + "\n\nLanguage profile constraints (context only):\n" + profile
    for unit, fingerprint, paths in pending:
        request = {
            "schema_version": "fullbook-translation-request-1.0", "phase": "6",
            "request_fingerprint": fingerprint, "provider": settings.translation_provider,
            "base_url": settings.translation_base_url, "model": settings.translation_model,
            "thinking_mode": settings.translation_thinking_mode,
            "prompt_version": settings.phase3c4_prompt_version,
            "prompt_sha256": prompt_hash, "language_profile_sha256": profile_hash,
            "api_key_env": key_name, "api_key_recorded": False,
            "payload": unit.provider_payload(), "automatic_retry": False,
            "started_at": _now(), "status": "in_flight",
        }
        atomic_write_json(paths["request"], request)
        request_paths.append(str(paths["request"].resolve()))
        entry = {
            "fingerprint": fingerprint, "target_block_id": unit.target_block_id,
            "started_at": _now(), "status": "in_flight", "automatic_retry": False,
        }
        ledger["content_calls_started"] = int(ledger.get("content_calls_started", 0)) + 1
        ledger.setdefault("entries", []).append(entry)
        ledger["updated_at"] = _now()
        atomic_write_json(ledger_path, ledger)
        checkpoint.increment_api_call("translation")
        calls += 1
        try:
            response = provider.translate_one(
                model=settings.translation_model, system_prompt=system_prompt,
                user_payload=unit.provider_payload(),
                max_output_tokens=settings.translation_max_output_tokens,
                temperature=settings.translation_temperature,
                thinking_mode=settings.translation_thinking_mode,
            )
            atomic_write_json(paths["raw"], response.raw_response)
            if response.usage is not None:
                atomic_write_json(paths["usage"], response.usage)
            normalized = normalize_phase3c4_translation(
                content=response.content, unit=unit, settings=settings,
                fingerprint=fingerprint, prompt_sha256=prompt_hash,
                profile_sha256=profile_hash, raw_path=paths["raw"],
                usage=response.usage, request_id=response.request_id,
            )
            atomic_write_json(paths["normalized"], normalized)
            atomic_write_json(paths["cache"], normalized)
            usage = response.usage or {}
            tokens += int(usage.get("total_tokens") or 0)
            request.update({"status": "completed", "ended_at": _now(), "retries": 0})
            atomic_write_json(paths["request"], request)
            entry.update({"status": "completed", "completed_at": _now()})
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            checkpoint.mark_completed("translation", unit.target_block_id)
            results.append(normalized)
            consecutive_signature = None
            consecutive_count = 0
        except Exception as exc:
            signature = type(exc).__name__
            safe = str(exc).replace(key, "[REDACTED]")[:1000]
            consecutive_count = consecutive_count + 1 if signature == consecutive_signature else 1
            consecutive_signature = signature
            failed.append(unit.target_block_id)
            entry.update({"status": "failed", "error_type": signature, "completed_at": _now()})
            ledger["updated_at"] = _now()
            atomic_write_json(ledger_path, ledger)
            atomic_write_json(paths["error"], {
                "target_block_id": unit.target_block_id, "request_fingerprint": fingerprint,
                "error_type": signature, "error_message": safe, "api_called": True,
                "automatic_retry": False, "recorded_at": _now(),
            })
            checkpoint.mark_quarantine("translation", unit.target_block_id, safe)
            if consecutive_count >= 3:
                raise RuntimeError(f"three consecutive {signature} translation failures; stopped") from exc
    order = {unit.target_block_id: index for index, unit in enumerate(units)}
    results.sort(key=lambda item: order[item.target_block_id])
    return FullbookTranslationBatchResult(
        results=results, api_calls_this_run=calls, cache_hits=cache_hits,
        failed_block_ids=failed, pending_block_ids=[], request_paths=request_paths,
        total_tokens_this_run=tokens,
    )
